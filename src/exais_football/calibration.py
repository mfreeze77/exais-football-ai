"""Reviewed field projections and conservative preflight; never automatic tactical truth."""

from __future__ import annotations

import json
import math
from uuid import uuid4

from .cv_models import CalibrationRequest
from .frames import frame_index, verified_source
from .geometry import homography, hull_area, inverse, project
from .service import CatalogError, encode, now
from .study import token


class Calibration:
    def __init__(self, catalog):
        self.catalog = catalog

    def latest_revision(self, db, clip_id):
        return db.execute(
            "SELECT coalesce(max(revision),0) FROM calibrations WHERE clip_id=?", (clip_id,)
        ).fetchone()[0]

    def list(self, clip_id):
        with self.catalog.db.connection() as db:
            self.catalog._require(db, "clips", clip_id)
            return [
                {**dict(r), "payload": json.loads(r["payload"])}
                for r in db.execute(
                    "SELECT * FROM calibrations WHERE clip_id=? ORDER BY revision DESC", (clip_id,)
                )
            ]

    def get(self, calibration_id):
        with self.catalog.db.connection() as db:
            row = db.execute("SELECT * FROM calibrations WHERE id=?", (calibration_id,)).fetchone()
            if not row:
                raise CatalogError(
                    "calibration_missing", "Choose an existing saved calibration", 404
                )
            return {**dict(row), "payload": json.loads(row["payload"])}

    def preview(self, clip_id, body):
        clip, _ = verified_source(self.catalog, clip_id)
        if body.source_sha256 != clip["source_sha256"]:
            raise CatalogError("source_mismatch", "Calibration is for a different video", 409)
        with self.catalog.db.connection() as db:
            revision = self.latest_revision(db, clip_id)
        if revision != body.expected_revision:
            raise CatalogError(
                "version_conflict", "Calibration changed; reload before editing", 409
            )
        index = frame_index(self.catalog, clip_id, create=True)
        if index.get("rotation_degrees", 0) % 360:
            raise CatalogError(
                "calibration_rotation",
                "This calibration profile requires unrotated native video",
                422,
            )
        if body.frame >= index["count"]:
            raise CatalogError("frame_out_of_range", "Select a frame in the source index", 422)
        points = [p.model_dump() for p in body.points]
        w, h = clip["width"], clip["height"]
        source = [[p["px"], p["py"]] for p in points]
        target = [[p["fx"], p["fy"]] for p in points]
        if any(not (0 <= x <= w and 0 <= y <= h) for x, y in source):
            raise CatalogError(
                "calibration_bounds", "Pixel points must lie inside the source image", 422
            )
        if hull_area(source) < w * h * 0.01 or hull_area(target) < 20:
            raise CatalogError(
                "calibration_spread",
                "Spread calibration points across multiple yard lines and the field width",
                422,
            )
        matrix, rms = homography(source, target)
        if rms > max(3.0, math.hypot(w, h) * 0.005):
            raise CatalogError(
                "calibration_residual",
                "Reprojection error is too large; review point correspondences",
                422,
            )
        inv = inverse(matrix)
        grid = []
        for x in range(0, 101, 5):
            try:
                grid.append([project(inv, x, 0), project(inv, x, 53.3333333333)])
            except CatalogError:
                continue
        for y in (0, 17.78, 35.55, 53.3333333333):
            try:
                grid.append([project(inv, 0, y), project(inv, 100, y)])
            except CatalogError:
                continue
        data = {
            "clip_id": clip_id,
            "source_sha256": clip["source_sha256"],
            "expected_revision": revision,
            "width": w,
            "height": h,
            "frame": body.frame,
            "at_seconds": index["timestamps"][body.frame],
            "points": points,
            "pixel_to_field": matrix,
            "reprojection_rms_pixels": rms,
            "grid": grid,
            "units": "yards; fx 0–100 between goal lines, fy 0–53⅓ across field",
            "method": "normalized_projective_least_squares",
            "approval": "requires_explicit_grid_review",
        }
        return {**data, "preview_token": token(data)}

    def save(self, clip_id, body):
        preview = self.preview(clip_id, body)
        if preview["preview_token"] != body.preview_token:
            raise CatalogError("preview_stale", "Reprojection preview changed; review again", 409)
        with self.catalog.db.connection(write=True) as db:
            if self.latest_revision(db, clip_id) != body.expected_revision:
                raise CatalogError(
                    "version_conflict", "Calibration changed during confirmation", 409
                )
            data = {**preview, "approval": "owner_reviewed"}
            cal_id = str(uuid4())
            revision = body.expected_revision + 1
            db.execute(
                "INSERT INTO calibrations VALUES(?,?,?,?,?,?)",
                (cal_id, clip_id, body.source_sha256, revision, encode(data), now()),
            )
        return self.get(cal_id)

    def preflight(self, clip_id, body):
        clip, _ = verified_source(self.catalog, clip_id)
        reasons = []
        if body.source_sha256 != clip["source_sha256"]:
            raise CatalogError(
                "source_mismatch", "Analysis intent belongs to a different source", 409
            )
        index = frame_index(self.catalog, clip_id, create=False)
        if index["status"] != "ready":
            reasons.append("Build the presentation frame index")
        elif index["count"] > 3000 or index.get("rotation_degrees", 0) % 360:
            reasons.append("Use an unrotated single-play source of at most 3000 decoded frames")
        cal = self.get(str(body.calibration_id)) if body.calibration_id else None
        if not cal:
            reasons.append("Save and review a field calibration")
        elif (
            cal["clip_id"] != clip_id
            or cal["source_sha256"] != clip["source_sha256"]
            or any(cal["payload"][v] != clip[v] for v in ("width", "height"))
        ):
            reasons.append("The calibration does not match this source and geometry")
        end = body.context_end if body.context_end is not None else clip["source_duration"]
        if not 0 <= body.context_start < end <= clip["source_duration"]:
            reasons.append("Choose a valid source-relative analysis context")
        if not body.context_reviewed:
            reasons.append("Review pre-snap context and camera continuity")
        if body.offense_direction == "unknown" or not body.offense_color.strip():
            reasons.append("Record offense direction and its visual team description")
        snap_time = None
        if (
            body.snap_frame is None
            or index["status"] != "ready"
            or body.snap_frame >= index["count"]
        ):
            reasons.append("Mark the snap on an indexed source frame")
        else:
            snap_time = index["timestamps"][body.snap_frame]
            if not body.context_start + 0.5 <= snap_time < end:
                reasons.append(
                    "Retain at least half a second of pre-snap setup inside the analysis context"
                )
        if any(body.context_start < t < end for t in body.camera_cuts):
            reasons.append("The context crosses a camera cut; create separate single-camera clips")
        if cal and not body.context_start <= cal["payload"]["at_seconds"] < end:
            reasons.append("Use a calibration frame within this context")
        # This pinned runner processes the complete source. Never pretend a narrowed
        # crop protects it from a cut elsewhere; use an independently imported clip.
        if body.context_start != 0 or end != clip["source_duration"] or body.camera_cuts:
            reasons.append(
                "The pinned runner requires a full single-play source with no camera cuts"
            )
        return {
            "status": "needs_input" if reasons else "ready",
            "reasons": reasons,
            "clip_id": clip_id,
            "source_sha256": clip["source_sha256"],
            "snap_at_seconds": snap_time,
            "calibration": cal,
            "intent": body.model_dump(mode="json"),
            "timeline_fingerprint": token(index.get("timestamps", [])),
            "automatic_tactical_claims": False,
        }

    def transfer_preview(self, clip_id, body):
        reference = self.get(str(body.source_calibration_id))
        refclip, _ = verified_source(self.catalog, reference["clip_id"])
        target, _ = verified_source(self.catalog, clip_id)
        if not refclip["game_id"] or refclip["game_id"] != target["game_id"]:
            raise CatalogError(
                "transfer_game",
                "Calibration transfer requires the same explicitly assigned game",
                422,
            )
        if body.target_source_sha256 != target["source_sha256"] or any(
            refclip[k] != target[k] for k in ("width", "height")
        ):
            raise CatalogError(
                "transfer_geometry",
                "Source hash or camera geometry does not match the transfer profile",
                422,
            )
        pairs = body.pairs
        a = [[p.reference_x, p.reference_y] for p in pairs]
        b = [[p.target_x, p.target_y] for p in pairs]
        for points in (a, b):
            if (
                any(
                    not 0 <= x <= target["width"] or not 0 <= y <= target["height"]
                    for x, y in points
                )
                or hull_area(points) < target["width"] * target["height"] * 0.03
            ):
                raise CatalogError(
                    "transfer_points",
                    "Use distributed, in-frame reference/target correspondences",
                    422,
                )
        matrix, rms = homography(a, b)
        if rms > 3:
            raise CatalogError(
                "transfer_quality", "Correspondence error exceeds three reference pixels", 422
            )
        points = []
        for p in reference["payload"]["points"]:
            x, y = project(matrix, p["px"], p["py"])
            points.append({"px": x, "py": y, "fx": p["fx"], "fy": p["fy"]})
        request = CalibrationRequest(
            source_sha256=body.target_source_sha256,
            expected_revision=body.target_expected_revision,
            frame=body.target_frame,
            points=points,
        )
        preview = self.preview(clip_id, request)
        result = {
            "source_calibration_id": reference["id"],
            "reference_source_sha256": refclip["source_sha256"],
            "reference_to_target": matrix,
            "correspondence_rms_pixels": rms,
            "request": request.model_dump(),
            "calibration_preview": preview,
            "method": "owner_supplied_feature_correspondences",
            "reference_calibration_revision": reference["revision"],
        }
        transfer_id = str(uuid4())
        result["preview_token"] = token(result)
        with self.catalog.db.connection(write=True) as db:
            db.execute(
                "INSERT INTO calibration_transfers VALUES(?,?,?,?,?)",
                (transfer_id, clip_id, encode(result), "needs_review", now()),
            )
        return {"id": transfer_id, "status": "needs_review", **result}

    def transfer_apply(self, transfer_id, body):
        from .cv_models import CalibrationSave

        with self.catalog.db.connection() as db:
            row = db.execute(
                "SELECT * FROM calibration_transfers WHERE id=?", (transfer_id,)
            ).fetchone()
            if not row:
                raise CatalogError(
                    "transfer_missing", "This transfer candidate does not exist", 404
                )
            payload = json.loads(row["payload"])
        if row["status"] != "needs_review" or body.preview_token != payload["preview_token"]:
            raise CatalogError("preview_stale", "Transfer already decided or preview changed", 409)
        reference = self.get(payload["source_calibration_id"])
        verified_source(self.catalog, reference["clip_id"])
        request = CalibrationSave(
            **payload["request"], preview_token=payload["calibration_preview"]["preview_token"]
        )
        # One transaction makes target calibration publication and transfer decision
        # indivisible, rather than saving through a nested independent transaction.
        preview = self.preview(row["target_clip_id"], request)
        if preview["preview_token"] != request.preview_token:
            raise CatalogError(
                "preview_stale", "Target changed; create another transfer candidate", 409
            )
        result = None
        with self.catalog.db.connection(write=True) as db:
            current = db.execute(
                "SELECT status FROM calibration_transfers WHERE id=?", (transfer_id,)
            ).fetchone()[0]
            if (
                current != "needs_review"
                or self.latest_revision(db, row["target_clip_id"]) != request.expected_revision
            ):
                raise CatalogError(
                    "preview_stale", "A concurrent calibration decision changed this candidate", 409
                )
            if body.decision == "accepted":
                cal_id = str(uuid4())
                data = {
                    **preview,
                    "approval": "owner_reviewed_transfer",
                    "transfer_id": transfer_id,
                }
                db.execute(
                    "INSERT INTO calibrations VALUES(?,?,?,?,?,?)",
                    (
                        cal_id,
                        row["target_clip_id"],
                        request.source_sha256,
                        request.expected_revision + 1,
                        encode(data),
                        now(),
                    ),
                )
                result = cal_id
            db.execute(
                "UPDATE calibration_transfers SET status=? WHERE id=?", (body.decision, transfer_id)
            )
        return {"status": body.decision, "calibration": self.get(result) if result else None}
