"""Transactional film-study workflows with frozen inputs and explicit recovery."""

from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
from uuid import uuid4

from . import tagging
from .models import ClipPatch
from .service import Catalog, CatalogError, encode, now
from .study_models import (
    BatchRequest,
    ClipFilters,
    ComparisonCreate,
    DrawingSave,
    SavedSearchCreate,
    SessionProgress,
    SnapshotCreate,
    ViewCreate,
)


def token(value: object) -> str:
    return hashlib.sha256(encode(value).encode()).hexdigest()


def require(connection: sqlite3.Connection, table: str, key: str):
    if table not in {
        "batch_edits",
        "saved_searches",
        "study_sessions",
        "comparisons",
        "cv_runs",
        "work_jobs",
        "calibrations",
        "calibration_transfers",
        "artifact_records",
    }:
        raise ValueError("Unsupported record table")
    row = connection.execute(f"SELECT * FROM {table} WHERE id=?", (key,)).fetchone()
    if row is None:
        raise CatalogError("study_not_found", "This film-study record no longer exists", 404)
    return row


class Study:
    def __init__(self, catalog: Catalog):
        self.catalog, self.db = catalog, catalog.db

    def _batch_plan(self, connection, request: BatchRequest) -> dict:
        changes = request.changes.model_dump(mode="json", exclude_unset=True)
        plans = []
        for selected in request.clips:
            clip = self.catalog._clip(connection, str(selected.clip_id))
            if clip["version"] != selected.version:
                raise CatalogError(
                    "batch_conflict",
                    f"Clip {clip['id']} changed. No batch edits were applied; preview again",
                    409,
                )
            after = dict(changes)
            if "game_id" in after and after["game_id"] is not None:
                self.catalog._require(connection, "games", after["game_id"])
            for player_id in after.get("player_ids", []):
                self.catalog._require(connection, "players", player_id)
            if "tags" in after:
                edit = after["tags"]
                values = {
                    row["name"] if (row := tagging.resolve(connection, name)) else name
                    for name in edit["values"]
                }
                current = set(clip["tags"])
                after["tags"] = sorted(
                    values
                    if edit["mode"] == "replace"
                    else current | values
                    if edit["mode"] == "add"
                    else current - values
                )
                if len(after["tags"]) > 40:
                    raise CatalogError("tag_limit", "A clip may have at most 40 tags", 422)
            before = {k: clip[k] for k in after}
            # Record only changed fields: undo must never restore an entire old clip.
            after = {k: v for k, v in after.items() if v != before[k]}
            before = {k: before[k] for k in after}
            plans.append(
                {
                    "clip_id": clip["id"],
                    "title": clip["title"],
                    "version": clip["version"],
                    "before": before,
                    "after": after,
                }
            )
        intent = request.model_dump(mode="json", exclude={"preview_token"})
        plan = {"policy": "all_or_nothing", "intent": intent, "clips": plans}
        return {**plan, "preview_token": token(plan)}

    def preview_batch(self, request: BatchRequest) -> dict:
        with self.db.connection() as connection:
            return self._batch_plan(connection, request)

    def apply_batch(self, request, preview_token: str) -> dict:
        with self.db.connection(write=True) as connection:
            plan = self._batch_plan(connection, request)
            if plan["preview_token"] != preview_token:
                raise CatalogError("preview_stale", "Batch preview changed; preview again", 409)
            for item in plan["clips"]:
                if item["after"]:
                    clip = self.catalog._patch(
                        connection,
                        item["clip_id"],
                        ClipPatch(version=item["version"], **item["after"]),
                    )
                    item["applied_version"] = clip["version"]
                else:
                    item["applied_version"] = item["version"]
            batch_id = str(uuid4())
            connection.execute(
                "INSERT INTO batch_edits(id,payload,created_at) VALUES(?,?,?)",
                (batch_id, encode(plan), now()),
            )
            return {"id": batch_id, "undone": False, **plan}

    def batches(self) -> list[dict]:
        with self.db.connection() as connection:
            return [
                {
                    "id": row["id"],
                    "undone": bool(row["undone"]),
                    "created_at": row["created_at"],
                    "clips": len(json.loads(row["payload"])["clips"]),
                }
                for row in connection.execute(
                    "SELECT * FROM batch_edits ORDER BY created_at DESC LIMIT 100"
                )
            ]

    def undo_batch(self, batch_id: str) -> dict:
        with self.db.connection(write=True) as connection:
            record = require(connection, "batch_edits", batch_id)
            if record["undone"]:
                raise CatalogError(
                    "batch_already_undone", "This batch has already been undone", 409
                )
            plan = json.loads(record["payload"])
            for item in plan["clips"]:
                if not item["after"]:
                    continue
                current = self.catalog._clip(connection, item["clip_id"])
                if current["version"] != item["applied_version"]:
                    raise CatalogError(
                        "undo_conflict",
                        f"Clip {current['id']} changed after this batch; nothing was undone",
                        409,
                    )
            for item in plan["clips"]:
                if item["after"]:
                    self.catalog._patch(
                        connection,
                        item["clip_id"],
                        ClipPatch(version=item["applied_version"], **item["before"]),
                    )
            connection.execute(
                "UPDATE batch_edits SET undone=1,undone_at=? WHERE id=?", (now(), batch_id)
            )
            return {
                "id": batch_id,
                "undone": True,
                "changed_clips": sum(bool(i["after"]) for i in plan["clips"]),
            }

    def create_view(self, clip_id: str, request: ViewCreate) -> dict:
        with self.db.connection(write=True) as connection:
            current = self.catalog._clip(connection, clip_id)
            if current["version"] != request.version:
                raise CatalogError(
                    "version_conflict", "Source view changed; reload before creating a cut", 409
                )
            if not 0 <= request.start_seconds < request.end_seconds <= current["source_duration"]:
                raise CatalogError(
                    "invalid_range", "The cut must remain inside its original source", 422
                )
            asset = self.catalog._require(connection, "clips", clip_id)["asset_id"]
            new_id, timestamp = str(uuid4()), now()
            connection.execute(
                """INSERT INTO clips(id,asset_id,game_id,title,start_seconds,end_seconds,
                                created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)""",
                (
                    new_id,
                    asset,
                    current["game_id"],
                    request.title,
                    request.start_seconds,
                    request.end_seconds,
                    timestamp,
                    timestamp,
                ),
            )
            if request.inherit_labels:
                connection.executemany(
                    "INSERT INTO clip_tags(clip_id,tag) VALUES(?,?)",
                    [(new_id, t) for t in current["tags"]],
                )
                connection.executemany(
                    "INSERT INTO clip_players(clip_id,player_id) VALUES(?,?)",
                    [(new_id, p) for p in current["player_ids"]],
                )
                connection.execute(
                    "UPDATE clips SET phase=? WHERE id=?", (current["phase"], new_id)
                )
            connection.execute(
                "INSERT INTO view_origins VALUES(?,?,?,?)",
                (new_id, clip_id, current["source_sha256"], timestamp),
            )
            return self.catalog._record_revision(connection, new_id)

    def views(self, clip_id: str) -> list[dict]:
        with self.db.connection() as connection:
            asset_id = self.catalog._require(connection, "clips", clip_id)["asset_id"]
            rows = connection.execute(
                "SELECT id FROM clips WHERE asset_id=? ORDER BY created_at,id", (asset_id,)
            ).fetchall()
            return [self.catalog._clip(connection, row[0]) for row in rows]

    def save_search(self, request: SavedSearchCreate, search_id: str | None = None) -> dict:
        with self.db.connection(write=True) as connection:
            filters = request.filters.model_dump(mode="json", exclude_none=True)
            if search_id is None:
                search_id = str(uuid4())
                connection.execute(
                    (
                        "INSERT INTO "
                        "saved_searches(id,title,filter_version,filter"
                        "s,created_at) VALUES(?,?,?,?,?)"
                    ),
                    (search_id, request.title, request.filter_version, encode(filters), now()),
                )
            else:
                row = require(connection, "saved_searches", search_id)
                if row["version"] != request.version:
                    raise CatalogError(
                        "version_conflict", "Saved search changed; reload before saving", 409
                    )
                connection.execute(
                    (
                        "UPDATE saved_searches SET "
                        "title=?,filters=?,filter_version=1,version=version+1 WHERE id=?"
                    ),
                    (request.title, encode(filters), search_id),
                )
            return self._search_record(require(connection, "saved_searches", search_id))

    @staticmethod
    def _search_record(row) -> dict:
        record = dict(row)
        if record["filter_version"] != 1:
            raise CatalogError(
                "filter_version_unsupported",
                "Upgrade the application to read this saved search",
                409,
            )
        record["filters"] = ClipFilters.model_validate(json.loads(record["filters"])).model_dump(
            mode="json", exclude_none=True
        )
        return record

    def searches(self) -> list[dict]:
        with self.db.connection() as connection:
            return [
                self._search_record(row)
                for row in connection.execute(
                    "SELECT * FROM saved_searches ORDER BY created_at DESC"
                )
            ]

    def search_results(self, search_id: str, limit: int = 60, offset: int = 0) -> dict:
        with self.db.connection() as connection:
            search = self._search_record(require(connection, "saved_searches", search_id))
        return {
            **self.catalog.list_clips(**search["filters"], limit=limit, offset=offset),
            "search": search,
        }

    def delete_search(self, search_id: str, version: int) -> dict:
        with self.db.connection(write=True) as connection:
            row = require(connection, "saved_searches", search_id)
            if row["version"] != version:
                raise CatalogError(
                    "version_conflict", "Saved search changed; reload before deleting", 409
                )
            connection.execute("DELETE FROM saved_searches WHERE id=?", (search_id,))
        return {"deleted": search_id}

    def _search_snapshot(self, connection, search_id: str) -> dict:
        search = self._search_record(require(connection, "saved_searches", search_id))
        results = self.catalog.list_clips(**search["filters"], limit=1001)
        if results["total"] > 1000:
            raise CatalogError(
                "collection_too_large",
                "Narrow the search to at most 1,000 clips before creating a fixed playlist",
                422,
            )
        data = {
            "search": search,
            "clips": [
                {"id": c["id"], "version": c["version"], "title": c["title"]}
                for c in results["items"]
            ],
        }
        return {**data, "preview_token": token(data)}

    def search_snapshot(self, search_id: str, preview_token: str | None = None) -> dict:
        with self.db.connection(write=preview_token is not None) as connection:
            plan = self._search_snapshot(connection, search_id)
            if preview_token is None:
                return plan
            if plan["preview_token"] != preview_token:
                raise CatalogError(
                    "preview_stale", "Dynamic results changed. Review the new playlist order", 409
                )
            playlist_id = str(uuid4())
            connection.execute(
                "INSERT INTO playlists VALUES(?,?,?,?)",
                (
                    playlist_id,
                    plan["search"]["title"],
                    "Fixed snapshot of saved search " + search_id,
                    now(),
                ),
            )
            connection.executemany(
                "INSERT INTO playlist_items VALUES(?,?,?)",
                [(playlist_id, c["id"], index) for index, c in enumerate(plan["clips"])],
            )
            return {"id": playlist_id, "clip_ids": [c["id"] for c in plan["clips"]]}

    def comparison(self, comparison_id: str) -> dict:
        with self.db.connection() as connection:
            row = require(connection, "comparisons", comparison_id)
            record = {**dict(row), **json.loads(row["payload"])}
            del record["payload"]
            record["stale"] = any(
                self.catalog._clip(connection, record[f"{side}_clip_id"])["version"]
                != record[f"{side}_snapshot"]["version"]
                for side in ("left", "right")
            )
            return record

    def comparisons(self) -> list[dict]:
        with self.db.connection() as connection:
            ids = [
                row[0]
                for row in connection.execute(
                    "SELECT id FROM comparisons ORDER BY created_at DESC LIMIT 100"
                )
            ]
        return [self.comparison(i) for i in ids]

    def save_comparison(self, request: ComparisonCreate, comparison_id: str | None = None) -> dict:
        with self.db.connection(write=True) as connection:
            payload = request.model_dump(mode="json", exclude={"version", "title"})
            for side in ("left", "right"):
                clip = self.catalog._clip(connection, payload[f"{side}_clip_id"])
                anchor = payload[f"{side}_anchor"]
                if not clip["start_seconds"] <= anchor < clip["end_seconds"]:
                    raise CatalogError(
                        "anchor_outside_view",
                        "Choose synchronization anchors inside each selected interval",
                        422,
                    )
                payload[f"{side}_snapshot"] = clip
            # Shared elapsed time u maps to source timestamp anchor + u.
            payload["offset_seconds"] = payload["right_anchor"] - payload["left_anchor"]
            payload["elapsed_start"] = max(
                payload[f"{s}_snapshot"]["start_seconds"] - payload[f"{s}_anchor"]
                for s in ("left", "right")
            )
            payload["elapsed_end"] = min(
                payload[f"{s}_snapshot"]["end_seconds"] - payload[f"{s}_anchor"]
                for s in ("left", "right")
            )
            if comparison_id is None:
                comparison_id, timestamp = str(uuid4()), now()
                connection.execute(
                    (
                        "INSERT INTO "
                        "comparisons(id,title,left_clip_id,right_clip_"
                        "id,payload,created_at,updated_at)\n           "
                        "                           VALUES(?,?,?,?,?,?,?)"
                    ),
                    (
                        comparison_id,
                        request.title,
                        str(request.left_clip_id),
                        str(request.right_clip_id),
                        encode(payload),
                        timestamp,
                        timestamp,
                    ),
                )
            else:
                row = require(connection, "comparisons", comparison_id)
                if row["version"] != request.version:
                    raise CatalogError(
                        "version_conflict", "Comparison changed; reload before saving", 409
                    )
                connection.execute(
                    (
                        "UPDATE comparisons SET "
                        "title=?,left_clip_id=?,right_clip_id=?,payloa"
                        "d=?,version=version+1,updated_at=? WHERE id=?"
                    ),
                    (
                        request.title,
                        str(request.left_clip_id),
                        str(request.right_clip_id),
                        encode(payload),
                        now(),
                        comparison_id,
                    ),
                )
        return self.comparison(comparison_id)

    def create_session(self, playlist_id: str) -> dict:
        with self.db.connection(write=True) as connection:
            playlist = self.catalog._require(connection, "playlists", playlist_id)
            snapshot = [
                self.catalog._clip(connection, row[0])
                for row in connection.execute(
                    "SELECT clip_id FROM playlist_items WHERE playlist_id=? ORDER BY ordinal",
                    (playlist_id,),
                )
            ]
            if not snapshot:
                raise CatalogError("playlist_empty", "Add clips to this playlist first", 422)
            session_id, timestamp = str(uuid4()), now()
            progress = {
                "index": 0,
                "at_seconds": snapshot[0]["start_seconds"],
                "replays": {},
                "watch_again": [],
                "finished": False,
            }
            connection.execute(
                (
                    "INSERT INTO "
                    "study_sessions(id,playlist_id,snapshot,progress,c"
                    "reated_at,updated_at) VALUES(?,?,?,?,?,?)"
                ),
                (
                    session_id,
                    playlist_id,
                    encode({"title": playlist["title"], "clips": snapshot}),
                    encode(progress),
                    timestamp,
                    timestamp,
                ),
            )
        return self.session(session_id)

    def session(self, session_id: str) -> dict:
        with self.db.connection() as connection:
            row = require(connection, "study_sessions", session_id)
            record = dict(row)
            record["snapshot"] = json.loads(record["snapshot"])
            record["progress"] = json.loads(record["progress"])
            availability = []
            for clip in record["snapshot"]["clips"]:
                current = connection.execute(
                    "SELECT archived FROM clips WHERE id=?", (clip["id"],)
                ).fetchone()
                if current is None:
                    status = "missing_reference"
                elif current["archived"]:
                    status = "archived"
                else:
                    try:
                        self.catalog.source_path(clip["id"])
                        status = "available"
                    except CatalogError:
                        status = "source_missing"
                availability.append({"clip_id": clip["id"], "status": status})
            record["availability"] = availability
            return record

    def sessions(self) -> list[dict]:
        with self.db.connection() as connection:
            return [
                {
                    "id": row["id"],
                    "playlist_id": row["playlist_id"],
                    "version": row["version"],
                    "title": json.loads(row["snapshot"])["title"],
                    "progress": json.loads(row["progress"]),
                }
                for row in connection.execute(
                    "SELECT * FROM study_sessions ORDER BY updated_at DESC LIMIT 100"
                )
            ]

    def update_session(self, session_id: str, request: SessionProgress) -> dict:
        with self.db.connection(write=True) as connection:
            row = require(connection, "study_sessions", session_id)
            if row["version"] != request.version:
                raise CatalogError(
                    "version_conflict", "This study session changed in another tab", 409
                )
            clips, progress = json.loads(row["snapshot"])["clips"], json.loads(row["progress"])
            if request.index >= len(clips):
                raise CatalogError(
                    "session_index", "Choose a clip from the frozen session order", 422
                )
            clip = clips[request.index]
            if not clip["start_seconds"] <= request.at_seconds <= clip["end_seconds"]:
                raise CatalogError(
                    "invalid_range", "Resume time must be inside this session cut", 422
                )
            if request.action == "reset":
                progress = {
                    "index": 0,
                    "at_seconds": clips[0]["start_seconds"],
                    "replays": {},
                    "watch_again": [],
                    "finished": False,
                }
            else:
                progress.update(index=request.index, at_seconds=request.at_seconds)
                if request.action == "replay":
                    progress["replays"][clip["id"]] = progress["replays"].get(clip["id"], 0) + 1
                if request.action == "watch_again":
                    progress["watch_again"] = list(
                        dict.fromkeys([*progress["watch_again"], clip["id"]])
                    )
                if request.action == "clear_watch_again":
                    progress["watch_again"] = [
                        i for i in progress["watch_again"] if i != clip["id"]
                    ]
                if request.action == "finish":
                    progress["finished"] = True
            connection.execute(
                "UPDATE study_sessions SET progress=?,version=version+1,updated_at=? WHERE id=?",
                (encode(progress), now(), session_id),
            )
        return self.session(session_id)

    def _drawings(self, connection, clip_id: str, revision: int | None = None) -> dict:
        clip = self.catalog._clip(connection, clip_id)
        if revision is None:
            row = connection.execute(
                "SELECT * FROM drawing_revisions WHERE clip_id=? ORDER BY revision DESC LIMIT 1",
                (clip_id,),
            ).fetchone()
        else:
            row = connection.execute(
                "SELECT * FROM drawing_revisions WHERE clip_id=? AND revision=?",
                (clip_id, revision),
            ).fetchone()
            if row is None:
                raise CatalogError(
                    "drawing_revision_missing", "This drawing revision is unavailable", 404
                )
        return {
            "clip_id": clip_id,
            "source_sha256": clip["source_sha256"],
            "width": clip["width"],
            "height": clip["height"],
            "version": row["revision"] if row else 0,
            "drawings": json.loads(row["payload"]) if row else [],
        }

    def drawings(self, clip_id: str, revision: int | None = None) -> dict:
        with self.db.connection() as connection:
            return self._drawings(connection, clip_id, revision)

    def save_drawings(self, clip_id: str, request: DrawingSave) -> dict:
        with self.db.connection(write=True) as connection:
            current = self._drawings(connection, clip_id)
            if request.version != current["version"]:
                raise CatalogError(
                    "version_conflict", "Drawings changed; reload before saving", 409
                )
            if request.source_sha256 != current["source_sha256"]:
                raise CatalogError(
                    "source_mismatch", "These drawings belong to a different source", 422
                )
            clip = self.catalog._clip(connection, clip_id)
            if any(d.end_seconds > clip["source_duration"] for d in request.drawings):
                raise CatalogError(
                    "invalid_range", "Drawing visibility must stay inside the source video", 422
                )
            connection.execute(
                "INSERT INTO drawing_revisions VALUES(?,?,?,?)",
                (
                    clip_id,
                    current["version"] + 1,
                    encode([d.model_dump(mode="json") for d in request.drawings]),
                    now(),
                ),
            )
            return self._drawings(connection, clip_id)

    def restore_drawings(self, clip_id: str, version: int, restore_revision: int) -> dict:
        # save_drawings rechecks the head under its write lock, so no lost update.
        snapshot = self.drawings(clip_id, restore_revision)
        return self.save_drawings(
            clip_id,
            DrawingSave(
                version=version,
                source_sha256=snapshot["source_sha256"],
                drawings=snapshot["drawings"],
            ),
        )

    def save_snapshot(self, clip_id: str, request: SnapshotCreate) -> dict:
        from .workspace import publish_artifact, validate_png

        with self.db.connection() as connection:
            clip = self.catalog._clip(connection, clip_id)
            drawings = self._drawings(connection, clip_id)
            if (
                request.source_sha256 != clip["source_sha256"]
                or drawings["version"] != request.drawing_revision
            ):
                raise CatalogError(
                    "snapshot_stale",
                    "Source or drawing revision changed. Capture the still again",
                    409,
                )
            if request.at_seconds > clip["source_duration"]:
                raise CatalogError("invalid_range", "Snapshot time lies outside the source", 422)
        try:
            content = base64.b64decode(request.png_base64, validate=True)
        except ValueError as exc:
            raise CatalogError("invalid_png", "Supply a valid PNG snapshot", 422) from exc
        width, height = validate_png(content)
        return publish_artifact(
            self.catalog,
            "annotated_still",
            clip_id,
            content,
            ".png",
            {
                "schema_version": 1,
                "origin": "reviewed_browser_canvas",
                "clip_id": clip_id,
                "source_asset_id": clip["source_asset_id"],
                "source_sha256": request.source_sha256,
                "at_seconds": request.at_seconds,
                "drawing_revision": request.drawing_revision,
                "drawings": drawings["drawings"],
                "width": width,
                "height": height,
            },
        )
