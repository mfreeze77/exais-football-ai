"""Leakage-aware, reproducible position evaluation. Fixture tests are not accuracy claims."""

from __future__ import annotations

import math

from .cv_store import POSITIONS
from .service import CatalogError
from .study import token


def wilson(successes, total):
    if not total:
        return None
    z = 1.959963984540054
    p = successes / total
    denominator = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denominator
    half = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    return [max(0, center - half), min(1, center + half)]


def evaluate(data):
    """Score explicitly labeled held-out plays, retaining unknown/failed predictions."""
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise CatalogError("evaluation_schema", "Use evaluation schema version 1", 422)
    plays = data.get("plays")
    if not isinstance(plays, list) or not 1 <= len(plays) <= 10000:
        raise CatalogError("evaluation_schema", "Provide 1 to 10,000 labeled plays", 422)
    seen, sources, rows, failures, train, test, runtimes = set(), {}, [], [], [], [], []
    labels = sorted(POSITIONS | {"unknown"})
    for play in plays:
        if not isinstance(play, dict):
            raise CatalogError("evaluation_schema", "Each play must be a record", 422)
        key, source, split = play.get("play_id"), play.get("source_sha256"), play.get("split")
        if not isinstance(key, str) or not key or key in seen or split not in {"train", "test"}:
            raise CatalogError(
                "evaluation_split", "Play IDs must be unique with explicit train/test splits", 422
            )
        if (
            not isinstance(source, str)
            or len(source) != 64
            or any(c not in "0123456789abcdef" for c in source)
        ):
            raise CatalogError("evaluation_source", "Every play needs its source SHA-256", 422)
        if source in sources and sources[source] != split:
            raise CatalogError(
                "evaluation_leakage", "Cuts of the same source cannot cross train/test splits", 422
            )
        seen.add(key)
        sources[source] = split
        (train if split == "train" else test).append(key)
        samples = play.get("labels", [])
        if not isinstance(samples, list) or len(samples) > 1000:
            raise CatalogError(
                "evaluation_labels", "Provide at most 1,000 manual labels per play", 422
            )
        ids = set()
        for sample in samples:
            if not isinstance(sample, dict) or not isinstance(sample.get("track_id"), str):
                raise CatalogError(
                    "evaluation_labels", "Each label needs an explicit track ID", 422
                )
            track = sample["track_id"]
            actual, predicted = sample.get("actual"), sample.get("predicted", "unknown")
            if actual not in POSITIONS or predicted not in labels or track in ids:
                raise CatalogError(
                    "evaluation_labels", "Labels must be supported and unique within each play", 422
                )
            ids.add(track)
            if not sample.get("reviewed_by") or not sample.get("evidence"):
                raise CatalogError(
                    "evaluation_ground_truth",
                    "Ground truth requires reviewer and source evidence",
                    422,
                )
            if split == "test":
                rows.append((key, actual, predicted))
        if split == "test":
            if play.get("failure"):
                failures.append({"play_id": key, "reason": str(play["failure"])[:1000]})
            runtime = play.get("runtime_seconds")
            if runtime is not None:
                if type(runtime) not in (int, float) or not math.isfinite(runtime) or runtime < 0:
                    raise CatalogError(
                        "evaluation_runtime", "Runtime must be a finite nonnegative number", 422
                    )
                runtimes.append(runtime)
    if not test or not rows:
        raise CatalogError(
            "evaluation_empty", "Provide independently reviewed labels in the test split", 422
        )
    confusion = {a: {b: 0 for b in labels} for a in sorted(POSITIONS)}
    for _, actual, predicted in rows:
        confusion[actual][predicted] += 1
    per_class = {}
    correct = sum(a == p for _, a, p in rows)
    for actual, counts in confusion.items():
        total, hits = sum(counts.values()), counts[actual]
        per_class[actual] = {
            "count": total,
            "correct": hits,
            "recall": hits / total if total else None,
            "wilson_95": wilson(hits, total),
        }
    # Scores are descriptive. Play clusters violate iid assumptions; explicitly
    # distinguish the binomial interval from a generalization guarantee.
    profile = data.get("profile", {})
    return {
        "schema_version": 1,
        "dataset_sha256": token(data),
        "profile": profile,
        "training_plays": train,
        "test_plays": test,
        "labels": len(rows),
        "correct": correct,
        "accuracy": correct / len(rows),
        "unknown_count": sum(p == "unknown" for _, _, p in rows),
        "confusion_matrix": confusion,
        "per_class": per_class,
        "binomial_wilson_95": wilson(correct, len(rows)),
        "uncertainty_note": (
            "Binomial intervals are descriptive; labels within plays "
            "are correlated. Do not generalize beyond reviewed profiles."
        ),
        "failed_plays": failures,
        "mean_runtime_seconds": sum(runtimes) / len(runtimes) if runtimes else None,
        "owner_acceptance": "requires_representative_owner_film_and_explicit_release_review",
        "profile_qualified": False,
    }
