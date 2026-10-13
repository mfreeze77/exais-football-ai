"""Bounded explicit identity views, separate from manual whole-clip player tags."""

from __future__ import annotations

import json


def reviewed_intervals(decisions: list[dict], player_id: str, start: float, end: float):
    """Resolve the newest identity decision on each interval, including revocation."""
    boundaries = {start, end}
    for decision in decisions:
        payload = decision["payload"]
        boundaries.update(
            max(start, min(end, payload[key])) for key in ("start_seconds", "end_seconds")
        )
    values = sorted(boundaries)
    matches = []
    for a, b in zip(values, values[1:], strict=False):
        if b <= a:
            continue
        middle = (a + b) / 2
        applicable = [
            d
            for d in decisions
            if d["payload"]["start_seconds"] <= middle < d["payload"]["end_seconds"]
        ]
        if not applicable:
            continue
        chosen = max(applicable, key=lambda d: d["revision"])
        if chosen["payload"].get("player_id") != player_id:
            continue
        item = {
            "start_seconds": a,
            "end_seconds": b,
            "decision_id": chosen["id"],
            "revision": chosen["revision"],
            "evidence": chosen["payload"]["evidence"],
        }
        if (
            matches
            and matches[-1]["decision_id"] == item["decision_id"]
            and matches[-1]["end_seconds"] == a
        ):
            matches[-1]["end_seconds"] = b
        else:
            matches.append(item)
    return matches


def player_identities(catalog, player_id: str, limit: int = 25, offset: int = 0):
    """Page candidate runs; revoked/stale identities may produce an empty page.

    next_offset is a candidate cursor, not a guessed total of accepted matches.
    No raw video, tracking file, or unbounded result set is read to render a page.
    """
    with catalog.db.connection() as db:
        player = dict(catalog._require(db, "players", player_id))
        runs = db.execute(
            "SELECT r.id,r.clip_id,r.source_sha256,r.continuity_version FROM cv_runs r "
            "JOIN clips c ON c.id=r.clip_id WHERE c.archived=0 AND EXISTS("
            "SELECT 1 FROM cv_decisions d WHERE d.run_id=r.id AND d.kind='identity' "
            "AND d.continuity_version=r.continuity_version "
            "AND json_extract(d.payload,'$.player_id')=?) "
            "ORDER BY r.created_at DESC,r.id LIMIT ? OFFSET ?",
            (player_id, limit + 1, offset),
        ).fetchall()
        result = []
        for run in runs[:limit]:
            clip = catalog._clip(db, run["clip_id"])
            records = db.execute(
                "SELECT * FROM cv_decisions WHERE run_id=? AND kind='identity' "
                "AND continuity_version=? ORDER BY revision",
                (run["id"], run["continuity_version"]),
            ).fetchall()
            tracks = {}
            for record in records:
                tracks.setdefault(record["track_id"], []).append(
                    {**dict(record), "payload": json.loads(record["payload"])}
                )
            matches = []
            for track_id, decisions in tracks.items():
                matches.extend(
                    {"track_id": track_id, **interval}
                    for interval in reviewed_intervals(
                        decisions, player_id, clip["start_seconds"], clip["end_seconds"]
                    )
                )
            if matches:
                result.append(
                    {
                        "kind": "reviewed_track_identity",
                        "run_id": run["id"],
                        "clip": clip,
                        "intervals": matches,
                        "source_sha256": run["source_sha256"],
                    }
                )
    return {
        "player": player,
        "items": result,
        "next_offset": offset + limit if len(runs) > limit else None,
        "manual_clip_tags_changed": False,
        "cursor_kind": "candidate_run_offset",
    }
