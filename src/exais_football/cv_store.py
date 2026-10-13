"""Complete, checksummed CV evidence bundles and immutable per-run human decisions."""

from __future__ import annotations

import json
import math
import shutil
import zipfile
from pathlib import Path
from uuid import uuid4

from .frames import frame_index, verified_source
from .recovery import inspect_package
from .service import CatalogError, encode, now
from .study import token
from .workspace import atomic_bytes, managed_path, read_json, workspace_lock

BUNDLE_KIND = "exais-football-analysis"
REQUIRED_FILES = {
    "detections.json",
    "tracks.json",
    "field_coords.json",
    "team_colors.json",
    "snap.json",
    "calibration.json",
    "labels.json",
    "positions.json",
}
POSITIONS = {"DL", "LB", "CB", "S", "OL", "QB", "RB", "WR"}
MAX_CV_FRAMES = 3000


def _finite(value):
    if isinstance(value, float) and not math.isfinite(value):
        raise CatalogError("analysis_nonfinite", "CV artifacts contain non-finite values", 422)
    if isinstance(value, list):
        for item in value:
            _finite(item)
    elif isinstance(value, dict):
        for item in value.values():
            _finite(item)


def validate_artifacts(manifest, data, index):
    if set(data) != REQUIRED_FILES:
        raise CatalogError(
            "analysis_incomplete",
            "Every detection/tracking/field/team/snap/calibration/label artifact is required",
            422,
        )
    for value in data.values():
        if not isinstance(value, dict):
            raise CatalogError(
                "analysis_shape", "CV artifacts must be structured JSON objects", 422
            )
        _finite(value)
    count = index["count"]
    width = index["width"]
    height = index["height"]
    if count > MAX_CV_FRAMES:
        raise CatalogError(
            "analysis_limits", "This profile supports at most 3000 decoded frames per play", 422
        )
    detections = data["detections.json"].get("frames")
    if not isinstance(detections, list) or len(detections) != count:
        raise CatalogError(
            "analysis_frames",
            "Detections must account for every source frame, including empty frames",
            422,
        )

    def box_valid(box):
        return (
            isinstance(box, list)
            and len(box) == 4
            and all(type(v) in {int, float} for v in box)
            and 0 <= box[0] < box[2] <= width
            and 0 <= box[1] < box[3] <= height
        )

    for i, frame in enumerate(detections):
        if (
            not isinstance(frame, dict)
            or type(frame.get("i")) is not int
            or frame["i"] != i
            or not isinstance(frame.get("boxes"), list)
            or len(frame["boxes"]) > 250
        ):
            raise CatalogError("analysis_frames", "Detection frame indexes/counts are invalid", 422)
        for box in frame["boxes"]:
            if (
                not isinstance(box, list)
                or len(box) != 5
                or not box_valid(box[:4])
                or type(box[4]) not in {int, float}
                or not 0 <= box[4] <= 1
            ):
                raise CatalogError(
                    "analysis_boxes", "Detection boxes or confidence are invalid", 422
                )
    tracks = data["tracks.json"].get("tracks")
    if not isinstance(tracks, list) or len(tracks) > 500:
        raise CatalogError("analysis_tracks", "Track count is invalid", 422)
    ids = set()
    for track in tracks:
        if (
            not isinstance(track, dict)
            or type(track.get("track_id")) is not int
            or track["track_id"] < 0
        ):
            raise CatalogError("analysis_tracks", "Raw tracks require nonnegative integer IDs", 422)
        key = str(track["track_id"])
        if key in ids:
            raise CatalogError("analysis_tracks", "Duplicate track IDs are not allowed", 422)
        ids.add(key)
        seen = -1
        if not isinstance(track.get("frames"), list) or not track["frames"]:
            raise CatalogError(
                "analysis_tracks", "Each track requires source-frame observations", 422
            )
        for frame in track["frames"]:
            if (
                not isinstance(frame, dict)
                or type(frame.get("i")) is not int
                or not seen < frame["i"] < count
                or not box_valid(frame.get("box"))
            ):
                raise CatalogError(
                    "analysis_tracks",
                    "Track observations must be bounded, ordered, unique source frames",
                    422,
                )
            seen = frame["i"]
    positions = data["positions.json"]
    if any(k not in ids or v not in POSITIONS for k, v in positions.items()):
        raise CatalogError(
            "analysis_positions",
            "Positions must reference known tracks and supported position tokens",
            422,
        )
    snap = data["snap.json"].get("frame")
    if type(snap) is not int or not 0 <= snap < count or snap != manifest.get("snap_frame"):
        raise CatalogError(
            "analysis_snap", "The recorded snap does not match the frozen source frame", 422
        )
    field = data["field_coords.json"]
    if (
        not isinstance(field.get("tracks"), list)
        or not isinstance(field.get("homographies"), list)
        or len(field["homographies"]) != count
    ):
        raise CatalogError(
            "analysis_field", "Full per-frame field projection evidence is required", 422
        )
    for i, item in enumerate(field["homographies"]):
        matrix = item.get("H") if isinstance(item, dict) else None
        if (
            not isinstance(item, dict)
            or item.get("i") != i
            or not isinstance(matrix, list)
            or len(matrix) != 3
            or any(
                not isinstance(r, list)
                or len(r) != 3
                or any(type(v) not in {int, float} for v in r)
                for r in matrix
            )
        ):
            raise CatalogError(
                "analysis_field", "Field homographies are malformed or incomplete", 422
            )
    field_ids = set()
    for tr in field["tracks"]:
        if (
            not isinstance(tr, dict)
            or str(tr.get("track_id")) not in ids
            or str(tr["track_id"]) in field_ids
        ):
            raise CatalogError(
                "analysis_field", "Field tracks must uniquely reference detection tracks", 422
            )
        field_ids.add(str(tr["track_id"]))
        raw = next(t for t in tracks if t["track_id"] == tr["track_id"])
        if (
            not isinstance(tr.get("frames"), list)
            or any(not isinstance(f, dict) for f in tr["frames"])
            or [f.get("i") for f in tr["frames"]] != [f["i"] for f in raw["frames"]]
        ):
            raise CatalogError(
                "analysis_field", "Field observations do not match raw track frame coverage", 422
            )
        for fr in tr["frames"]:
            if type(fr.get("x")) not in {int, float} or type(fr.get("y")) not in {int, float}:
                raise CatalogError(
                    "analysis_field", "Field coordinates must be finite numeric yards", 422
                )
    if field_ids != ids:
        raise CatalogError("analysis_field", "Every raw track requires field evidence", 422)
    calibration = data["calibration.json"]
    if not isinstance(calibration.get("points"), list) or len(calibration["points"]) < 4:
        raise CatalogError("analysis_calibration", "Saved calibration evidence is missing", 422)
    return tracks


class CVStore:
    def __init__(self, catalog):
        self.catalog = catalog

    def get(self, run_id):
        with self.catalog.db.connection() as db:
            row = db.execute("SELECT * FROM cv_runs WHERE id=?", (run_id,)).fetchone()
            if row is None:
                raise CatalogError("run_not_found", "This analysis run does not exist", 404)
            return {**dict(row), "manifest": json.loads(row["manifest"])}

    def list(self, clip_id):
        with self.catalog.db.connection() as db:
            self.catalog._require(db, "clips", clip_id)
            return [
                {k: v for k, v in self.get(r[0]).items() if k != "directory"}
                for r in db.execute(
                    "SELECT id FROM cv_runs WHERE clip_id=? ORDER BY created_at DESC LIMIT 50",
                    (clip_id,),
                )
            ]

    def ingest(self, clip_id, archive: Path):
        report = inspect_package(archive, expected_kind=BUNDLE_KIND, max_total_bytes=64 * 1024**2)
        manifest = report["manifest"]
        clip, _ = verified_source(self.catalog, clip_id)
        index = frame_index(self.catalog, clip_id, create=True)
        if (
            manifest.get("clip_id") != clip_id
            or manifest.get("source_sha256") != clip["source_sha256"]
            or manifest.get("source_asset_id") != clip["source_asset_id"]
        ):
            raise CatalogError(
                "analysis_source", "This analysis belongs to a different source or clip", 409
            )
        expected_timeline = {
            "kind": "source_relative_pts",
            "frame_count": index["count"],
            "pts_sha256": token(index["timestamps"]),
        }
        if (
            manifest.get("timeline") != expected_timeline
            or manifest.get("geometry") != {"width": clip["width"], "height": clip["height"]}
            or manifest.get("transform_chain")
            != [{"kind": "identity", "source_sha256": clip["source_sha256"]}]
        ):
            raise CatalogError(
                "analysis_transform",
                (
                    "Only explicitly verified native geometry and "
                    "one-to-one source-frame timelines are supported"
                ),
                422,
            )
        model = manifest.get("model")
        if (
            not isinstance(model, dict)
            or not model.get("backend")
            or not model.get("device")
            or not model.get("repository_revision")
            or not model.get("weights_sha256")
            or not model.get("weights_license")
        ):
            raise CatalogError(
                "analysis_model",
                "Complete environment, backend, device, and weight provenance is required",
                422,
            )
        if {f["path"] for f in manifest["files"]} != REQUIRED_FILES:
            raise CatalogError(
                "analysis_incomplete",
                "The complete allowlisted pipeline artifacts are required",
                422,
            )
        data = {}
        raw = {}
        with zipfile.ZipFile(archive) as bundle:
            for entry in manifest["files"]:
                raw[entry["path"]] = bundle.read(entry["path"])
                try:
                    data[entry["path"]] = json.loads(raw[entry["path"]])
                except (ValueError, UnicodeError) as exc:
                    raise CatalogError(
                        "analysis_json", "Analysis artifact is not valid JSON", 422
                    ) from exc
        validate_artifacts(manifest, data, index)
        run_id = str(uuid4())
        relative = f"analysis/{run_id}"
        directory = self.catalog.settings.path(relative)
        with workspace_lock(self.catalog):
            directory.mkdir(mode=0o700, exist_ok=False)
            try:
                for name, content in raw.items():
                    atomic_bytes(directory / name, content)
                with self.catalog.db.connection(write=True) as db:
                    db.execute(
                        (
                            "INSERT INTO "
                            "cv_runs(id,clip_id,source_sha256,manifest"
                            ",directory,created_at) VALUES(?,?,?,?,?,?)"
                        ),
                        (run_id, clip_id, clip["source_sha256"], encode(manifest), relative, now()),
                    )
            except BaseException:
                shutil.rmtree(directory)
                raise
        return {k: v for k, v in self.get(run_id).items() if k != "directory"}

    def artifact(self, run_id, name):
        if name not in REQUIRED_FILES:
            raise CatalogError("analysis_file", "Unknown analysis artifact", 404)
        run = self.get(run_id)
        item = next(f for f in run["manifest"]["files"] if f["path"] == name)
        path = managed_path(self.catalog.settings.data_dir, run["directory"] + "/" + name)
        from .service import file_sha256

        if file_sha256(path) != item["sha256"]:
            raise CatalogError(
                "analysis_changed", "A preserved analysis artifact failed its checksum", 409
            )
        return path

    def decisions(self, run_id):
        run = self.get(run_id)
        with self.catalog.db.connection() as db:
            rows = db.execute(
                "SELECT * FROM cv_decisions WHERE run_id=? ORDER BY revision,id", (run_id,)
            ).fetchall()
        return [
            {
                **dict(r),
                "payload": json.loads(r["payload"]),
                "stale": r["kind"] != "continuity"
                and r["continuity_version"] != run["continuity_version"],
            }
            for r in rows
        ]

    def projection(self, run_id):
        run = self.get(run_id)
        raw = read_json(self.artifact(run_id, "tracks.json"))["tracks"]
        positions = read_json(self.artifact(run_id, "positions.json"))
        decisions = self.decisions(run_id)
        index = frame_index(self.catalog, run["clip_id"], create=True)
        tracks = {
            str(tr["track_id"]): {
                "track_id": str(tr["track_id"]),
                "raw_track_ids": [str(tr["track_id"])],
                "frames": tr["frames"],
                "position": positions.get(str(tr["track_id"]), "unknown"),
                "suppressed": False,
            }
            for tr in raw
        }
        suppressed = {}
        splits = {}
        links = {}
        for d in decisions:
            if d["kind"] != "continuity":
                continue
            p = d["payload"]
            tid = d["track_id"]
            action = p["action"]
            if action in {"suppress", "restore"}:
                suppressed[tid] = action == "suppress"
            elif action == "split":
                splits.setdefault(tid, set()).add(p["at_frame"])
            elif action in {"link", "unlink"}:
                links[(tid, p["other_track_id"])] = action == "link"
        for (target, other), active in links.items():
            if active and target in tracks and other in tracks:
                a, b = tracks[target], tracks.pop(other)
                a["frames"] = sorted(a["frames"] + b["frames"], key=lambda f: f["i"])
                a["raw_track_ids"] += b["raw_track_ids"]
                if a["position"] != b["position"]:
                    a["position"] = "unknown"
        result = []
        for tid, tr in tracks.items():
            tr["suppressed"] = any(suppressed.get(raw_id, False) for raw_id in tr["raw_track_ids"])
            boundaries = sorted(splits.get(tid, set()))
            if boundaries:
                edges = [-1, *boundaries, index["count"]]
                for i in range(len(edges) - 1):
                    frames = [f for f in tr["frames"] if edges[i] <= f["i"] < edges[i + 1]]
                    if frames:
                        result.append(
                            {
                                **tr,
                                "track_id": tid if i == 0 else f"{tid}@{edges[i]}",
                                "frames": frames,
                            }
                        )
            else:
                result.append(tr)
        return {
            "run_id": run_id,
            "clip_id": run["clip_id"],
            "source_sha256": run["source_sha256"],
            "review_version": run["review_version"],
            "continuity_version": run["continuity_version"],
            "tracks": result,
            "decisions": decisions,
            "timestamps": index["timestamps"],
            "geometry": run["manifest"]["geometry"],
            "raw_immutable": True,
        }

    def decide(self, run_id, track_id, kind, body):
        projection = self.projection(run_id)
        tracks = {t["track_id"]: t for t in projection["tracks"]}
        raw = read_json(self.artifact(run_id, "tracks.json"))["tracks"]
        raw_ids = {str(t["track_id"]) for t in raw}
        if kind == "continuity":
            if track_id not in raw_ids:
                raise CatalogError(
                    "track_unknown", "Continuity decisions address a raw track ID", 422
                )
            if body.action in {"link", "unlink"}:
                if body.other_track_id not in raw_ids or body.other_track_id == track_id:
                    raise CatalogError(
                        "track_unknown", "Select another raw track in this same run", 422
                    )
                if body.action == "link":
                    if (
                        track_id not in tracks
                        or body.other_track_id not in tracks
                        or any(
                            t.startswith(track_id + "@") or t.startswith(body.other_track_id + "@")
                            for t in tracks
                        )
                    ):
                        raise CatalogError(
                            "continuity_conflict",
                            "Unlink existing chains and resolve splits before linking these tracks",
                            409,
                        )
                    if {f["i"] for f in tracks[track_id]["frames"]} & {
                        f["i"] for f in tracks[body.other_track_id]["frames"]
                    }:
                        raise CatalogError(
                            "continuity_overlap",
                            (
                                "Tracks with overlapping observations "
                                "cannot represent one continuous player"
                            ),
                            422,
                        )
            if body.action == "split":
                if (
                    track_id not in tracks
                    or len(tracks[track_id]["raw_track_ids"]) > 1
                    or body.at_frame is None
                    or not tracks[track_id]["frames"][0]["i"]
                    < body.at_frame
                    <= tracks[track_id]["frames"][-1]["i"]
                ):
                    raise CatalogError(
                        "continuity_split",
                        "Choose an interior source-frame boundary on an unlinked raw track",
                        422,
                    )
        else:
            if track_id not in tracks:
                raise CatalogError(
                    "track_unknown", "This track is not in the current run projection", 422
                )
            duration = self.catalog.get_clip(projection["clip_id"])["source_duration"]
            if not 0 <= body.start_seconds < body.end_seconds <= duration:
                raise CatalogError(
                    "decision_range", "Choose a source-relative interval inside this video", 422
                )
            if kind == "identity" and body.player_id is not None:
                with self.catalog.db.connection() as db:
                    self.catalog._require(db, "players", str(body.player_id))
        payload = body.model_dump(mode="json")
        payload.update(
            {
                "clip_id": projection["clip_id"],
                "run_id": run_id,
                "track_id": track_id,
                "source_sha256": projection["source_sha256"],
            }
        )
        if kind == "position":
            payload["original_suggestion"] = tracks[track_id]["position"]
        with self.catalog.db.connection(write=True) as db:
            run = db.execute("SELECT * FROM cv_runs WHERE id=?", (run_id,)).fetchone()
            if (
                run["review_version"] != body.expected_revision
                or run["review_version"] != projection["review_version"]
            ):
                raise CatalogError(
                    "version_conflict",
                    "Another review changed this run; reload before deciding",
                    409,
                )
            revision = run["review_version"] + 1
            continuity = run["continuity_version"] + int(kind == "continuity")
            db.execute(
                "INSERT INTO cv_decisions VALUES(?,?,?,?,?,?,?,?)",
                (
                    str(uuid4()),
                    run_id,
                    track_id,
                    kind,
                    encode(payload),
                    revision,
                    continuity,
                    now(),
                ),
            )
            db.execute(
                "UPDATE cv_runs SET review_version=?,continuity_version=? WHERE id=?",
                (revision, continuity, run_id),
            )
            db.execute(
                (
                    "UPDATE artifact_records SET stale=1 WHERE "
                    "kind='cv_overlay' AND json_extract(manifest,'$.run_id')=?"
                ),
                (run_id,),
            )
        return self.projection(run_id)

    def quality(self, run_id):
        projection = self.projection(run_id)
        flags = []
        for tr in projection["tracks"]:
            frames = tr["frames"]
            if len(frames) < 5:
                flags.append(
                    {"track_id": tr["track_id"], "kind": "short_fragment", "frame": frames[0]["i"]}
                )
            for a, b in zip(frames, frames[1:], strict=False):
                if b["i"] - a["i"] > 5:
                    flags.append(
                        {
                            "track_id": tr["track_id"],
                            "kind": "gap",
                            "frame": b["i"],
                            "missing_frames": b["i"] - a["i"] - 1,
                        }
                    )
                centers = [
                    ((f["box"][0] + f["box"][2]) / 2, (f["box"][1] + f["box"][3]) / 2)
                    for f in (a, b)
                ]
                if math.dist(*centers) > max(projection["geometry"].values()) * 0.2:
                    flags.append(
                        {"track_id": tr["track_id"], "kind": "large_jump", "frame": b["i"]}
                    )
        colors = read_json(self.artifact(run_id, "team_colors.json"))
        for event in colors.get("flip_events", [])[:1000]:
            flags.append({"kind": "possible_team_color_swap", "evidence": event})
        return {
            "run_id": run_id,
            "flags": flags[:2000],
            "truncated": len(flags) > 2000,
            "rule_version": 1,
            "interpretation": (
                "Heuristic review flags, not confirmed identity "
                "swaps. Occlusion and camera motion can cause false positives."
            ),
        }
