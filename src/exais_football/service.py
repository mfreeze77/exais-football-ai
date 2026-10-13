"""Transactional catalog operations; source media is never edited in place."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from .config import Settings
from .db import Database
from .media import MediaError, generate_thumbnail, probe_video
from .models import ClipPatch
from .tagging import canonical_tags

VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".mkv", ".avi", ".webm"}
EDITABLE = {
    "title",
    "description",
    "game_id",
    "phase",
    "review_status",
    "favorite",
    "archived",
    "start_seconds",
    "end_seconds",
    "tags",
    "player_ids",
}
CLIP_SELECT = """
SELECT c.*, a.duration AS source_duration, a.sha256 AS source_sha256,
       a.filename AS original_filename, a.width, a.height, a.fps, a.has_audio
FROM clips c JOIN assets a ON a.id=c.asset_id
"""


class CatalogError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        self.code, self.message, self.status = code, message, status
        super().__init__(message)


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


def encode(value) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class Catalog:
    def __init__(self, settings: Settings):
        self.settings = settings
        settings.prepare()
        self.db = Database(settings.path("catalog.sqlite3"))

    @staticmethod
    def _require(connection, table: str, record_id: str):
        if table not in {"clips", "games", "players", "playlists", "jobs"}:
            raise ValueError("Unknown catalog table")
        row = connection.execute(f"SELECT * FROM {table} WHERE id=?", (record_id,)).fetchone()
        if row is None:
            raise CatalogError("not_found", "The requested catalog record does not exist", 404)
        return row

    @staticmethod
    def _clip(connection: sqlite3.Connection, clip_id: str) -> dict:
        row = connection.execute(CLIP_SELECT + " WHERE c.id=?", (clip_id,)).fetchone()
        if row is None:
            raise CatalogError("clip_not_found", "This clip does not exist", 404)
        clip = dict(row)
        clip["source_asset_id"] = clip.pop("asset_id")
        for field in ("favorite", "archived", "has_audio"):
            clip[field] = bool(clip[field])
        clip["duration"] = clip["end_seconds"] - clip["start_seconds"]
        clip["tags"] = [
            r[0]
            for r in connection.execute(
                "SELECT tag FROM clip_tags WHERE clip_id=? ORDER BY tag", (clip_id,)
            )
        ]
        clip["player_ids"] = [
            r[0]
            for r in connection.execute(
                "SELECT player_id FROM clip_players WHERE clip_id=? ORDER BY player_id", (clip_id,)
            )
        ]
        clip["source_url"] = f"/api/clips/{clip_id}/source"
        clip["thumbnail_url"] = f"/api/clips/{clip_id}/thumbnail"
        return clip

    @classmethod
    def _record_revision(cls, connection, clip_id: str) -> dict:
        clip = cls._clip(connection, clip_id)
        connection.execute(
            "INSERT INTO revisions(clip_id,version,snapshot,created_at) VALUES(?,?,?,?)",
            (clip_id, clip["version"], encode(clip), clip["updated_at"]),
        )
        return clip

    def get_clip(self, clip_id: str) -> dict:
        with self.db.connection() as connection:
            return self._clip(connection, clip_id)

    def asset(self, clip_id: str) -> dict:
        with self.db.connection() as connection:
            self._require(connection, "clips", clip_id)
            row = connection.execute(
                "SELECT a.* FROM assets a JOIN clips c ON c.asset_id=a.id WHERE c.id=?",
                (clip_id,),
            ).fetchone()
            return dict(row)

    def source_path(self, clip_id: str) -> Path:
        path = self.settings.path(self.asset(clip_id)["path"])
        if not path.is_file():
            raise CatalogError("source_missing", "The original media file is missing", 404)
        return path

    def ingest(self, staged: Path, filename: str, title: str = "", game_id: str | None = None):
        filename = filename.replace("\\", "/").rsplit("/", 1)[-1]
        if not filename or len(filename) > 240 or any(ord(c) < 32 for c in filename):
            raise CatalogError("invalid_filename", "Choose a video with a normal filename")
        suffix = Path(filename).suffix.lower()
        if suffix not in VIDEO_EXTENSIONS:
            raise CatalogError("unsupported_video", "Use MP4, MOV, M4V, MKV, AVI or WebM", 415)
        title = title.strip() or Path(filename).stem
        if not title or len(title) > 240:
            raise CatalogError("invalid_title", "Clip titles must contain 1 to 240 characters")
        size = staged.stat().st_size
        if size == 0 or size > self.settings.max_upload_bytes:
            raise CatalogError("upload_size", "The upload is empty or exceeds the size limit", 413)
        source_hash = file_sha256(staged)
        with self.db.connection() as connection:
            if game_id:
                self._require(connection, "games", game_id)
            existing = connection.execute(
                "SELECT c.id FROM clips c JOIN assets a ON c.asset_id=a.id WHERE a.sha256=?",
                (source_hash,),
            ).fetchone()
            if existing:
                return {"clip": self._clip(connection, existing[0]), "duplicate": True}
        metadata = probe_video(staged)
        asset_id, clip_id, timestamp = str(uuid4()), str(uuid4()), now()
        relative = f"sources/{source_hash[:2]}/{source_hash}{suffix}"
        destination = self.settings.path(relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with self.db.connection(write=True) as connection:
            if game_id:
                self._require(connection, "games", game_id)
            existing = connection.execute(
                "SELECT c.id FROM clips c JOIN assets a ON c.asset_id=a.id WHERE a.sha256=?",
                (source_hash,),
            ).fetchone()
            if existing:
                return {"clip": self._clip(connection, existing[0]), "duplicate": True}
            # Published originals survive catalog rollback: another import may reuse one
            # as soon as the transaction releases its lock. Unreferenced bytes are safe
            # to verify and reuse after an interrupted or failed import.
            if destination.exists():
                if file_sha256(destination) != source_hash:
                    raise CatalogError("source_conflict", "An existing source failed verification")
            else:
                staged.replace(destination)
            connection.execute(
                """INSERT INTO assets(id,sha256,path,filename,size,duration,width,height,
                   fps,has_audio,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    asset_id,
                    source_hash,
                    relative,
                    filename,
                    size,
                    metadata["duration"],
                    metadata["width"],
                    metadata["height"],
                    metadata["fps"],
                    int(metadata["has_audio"]),
                    timestamp,
                ),
            )
            connection.execute(
                """INSERT INTO clips(id,asset_id,game_id,title,start_seconds,end_seconds,
                   created_at,updated_at) VALUES(?,?,?,?,0,?,?,?)""",
                (clip_id, asset_id, game_id, title, metadata["duration"], timestamp, timestamp),
            )
            clip = self._record_revision(connection, clip_id)
        try:
            generate_thumbnail(destination, self.settings.path(f"thumbnails/{clip_id}.jpg"))
        except (MediaError, OSError):
            # An optional preview failure must not discard a successfully imported original.
            pass
        return {"clip": clip, "duplicate": False}

    def list_clips(self, **filters) -> dict:
        where, parameters = ["c.archived=?"], [int(filters.get("archived", False))]
        for field in ("game_id", "phase", "review_status", "favorite"):
            if filters.get(field) is not None:
                where.append(f"c.{field}=?")
                parameters.append(filters[field])
        if filters.get("player_id"):
            where.append(
                "EXISTS(SELECT 1 FROM clip_players p WHERE p.clip_id=c.id AND p.player_id=?)"
            )
            parameters.append(filters["player_id"])
        if filters.get("tag"):
            where.append("""EXISTS(SELECT 1 FROM clip_tags t WHERE t.clip_id=c.id AND t.tag=
                COALESCE((SELECT n.name FROM tag_names a JOIN tag_names n
                ON n.tag_id=a.tag_id AND n.is_canonical=1 WHERE a.name=?),?))""")
            parameters.extend([" ".join(filters["tag"].casefold().split())] * 2)
        if filters.get("q"):
            query = filters["q"].replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            like = f"%{query}%"
            where.append("""(c.title LIKE ? ESCAPE '\\' OR c.description LIKE ? ESCAPE '\\'
                OR a.filename LIKE ? ESCAPE '\\'
                OR EXISTS(SELECT 1 FROM clip_tags t
                          WHERE t.clip_id=c.id AND (t.tag LIKE ? ESCAPE '\\'
                          OR EXISTS(SELECT 1 FROM tag_names n JOIN tag_names a
                          ON a.tag_id=n.tag_id WHERE n.name=t.tag AND n.is_canonical=1
                          AND a.name LIKE ? ESCAPE '\\')))
                OR EXISTS(SELECT 1 FROM notes n
                          WHERE n.clip_id=c.id AND n.body LIKE ? ESCAPE '\\'))""")
            parameters.extend([like, like, like, like, f"%{query.casefold()}%", like])
        clause = " WHERE " + " AND ".join(where)
        with self.db.connection() as connection:
            total = connection.execute(
                "SELECT count(*) FROM clips c JOIN assets a ON a.id=c.asset_id" + clause,
                parameters,
            ).fetchone()[0]
            rows = connection.execute(
                "SELECT c.id FROM clips c JOIN assets a ON a.id=c.asset_id"
                + clause
                + " ORDER BY c.updated_at DESC,c.id LIMIT ? OFFSET ?",
                [*parameters, filters.get("limit", 100), filters.get("offset", 0)],
            ).fetchall()
            return {"items": [self._clip(connection, row[0]) for row in rows], "total": total}

    def _patch(self, connection, clip_id: str, patch: ClipPatch) -> dict:
        current = self._clip(connection, clip_id)
        if patch.version != current["version"]:
            raise CatalogError("version_conflict", "This clip changed. Reload before saving", 409)
        changes = patch.model_dump(exclude_unset=True, mode="json")
        changes.pop("version")
        if not changes:
            return current
        if "tags" in changes:
            changes["tags"] = canonical_tags(connection, changes["tags"])
        updated = {**current, **changes}
        if not 0 <= updated["start_seconds"] < updated["end_seconds"] <= current["source_duration"]:
            raise CatalogError(
                "invalid_range", "Choose an in/out range inside the original video", 422
            )
        if updated["game_id"]:
            self._require(connection, "games", updated["game_id"])
        for player_id in updated["player_ids"]:
            self._require(connection, "players", player_id)
        for field in ("tags", "player_ids"):
            if field in changes:
                table, column = (
                    ("clip_tags", "tag") if field == "tags" else ("clip_players", "player_id")
                )
                connection.execute(f"DELETE FROM {table} WHERE clip_id=?", (clip_id,))
                connection.executemany(
                    f"INSERT INTO {table}(clip_id,{column}) VALUES(?,?)",
                    [(clip_id, item) for item in changes.pop(field)],
                )
        changes["version"] = current["version"] + 1
        changes["updated_at"] = now()
        assignments = ",".join(f"{field}=?" for field in changes)
        connection.execute(
            f"UPDATE clips SET {assignments} WHERE id=?", [*changes.values(), clip_id]
        )
        return self._record_revision(connection, clip_id)

    def patch_clip(self, clip_id: str, patch: ClipPatch) -> dict:
        with self.db.connection(write=True) as connection:
            return self._patch(connection, clip_id, patch)

    def history(self, clip_id: str) -> list[dict]:
        with self.db.connection() as connection:
            self._require(connection, "clips", clip_id)
            return [
                {
                    "version": row["version"],
                    "snapshot": json.loads(row["snapshot"]),
                    "created_at": row["created_at"],
                }
                for row in connection.execute(
                    "SELECT * FROM revisions WHERE clip_id=? ORDER BY version DESC", (clip_id,)
                )
            ]

    def restore(self, clip_id: str, version: int, current_version: int) -> dict:
        with self.db.connection(write=True) as connection:
            self._require(connection, "clips", clip_id)
            revision = connection.execute(
                "SELECT snapshot FROM revisions WHERE clip_id=? AND version=?", (clip_id, version)
            ).fetchone()
            if revision is None:
                raise CatalogError("revision_not_found", "This saved revision does not exist", 404)
            snapshot = json.loads(revision[0])
            patch = ClipPatch(version=current_version, **{key: snapshot[key] for key in EDITABLE})
            return self._patch(connection, clip_id, patch)

    def list_entities(self, table: str) -> list[dict]:
        if table not in {"games", "players", "playlists"}:
            raise ValueError("Unknown entity")
        order = "name" if table == "players" else "created_at DESC"
        with self.db.connection() as connection:
            rows = [
                dict(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY {order}")
            ]
            if table == "playlists":
                for row in rows:
                    row["clip_count"] = connection.execute(
                        "SELECT count(*) FROM playlist_items WHERE playlist_id=?", (row["id"],)
                    ).fetchone()[0]
            return rows

    def create_entity(self, table: str, fields: dict) -> dict:
        allowed = {
            "games": {"title", "opponent", "date", "season", "notes"},
            "players": {"name", "jersey", "position"},
            "playlists": {"title", "description"},
        }
        if table not in allowed or set(fields) != allowed[table]:
            raise ValueError("Invalid entity fields")
        record = {"id": str(uuid4()), **fields, "created_at": now()}
        columns = ",".join(record)
        placeholders = ",".join("?" for _ in record)
        with self.db.connection(write=True) as connection:
            connection.execute(
                f"INSERT INTO {table}({columns}) VALUES({placeholders})", list(record.values())
            )
        return record

    def stats(self) -> dict:
        with self.db.connection() as connection:
            row = connection.execute(
                """SELECT count(*) AS clips, coalesce(sum(favorite),0) AS favorites,
                   coalesce(sum(end_seconds-start_seconds),0) AS total_seconds
                   FROM clips WHERE archived=0"""
            ).fetchone()
            return {
                **dict(row),
                **{
                    table: connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                    for table in ("games", "players")
                },
            }

    def tags(self) -> list[dict]:
        with self.db.connection() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    """SELECT t.tag AS name,count(*) AS count FROM clip_tags t
                   JOIN clips c ON c.id=t.clip_id WHERE c.archived=0
                   GROUP BY t.tag ORDER BY t.tag"""
                )
            ]

    def notes(self, clip_id: str) -> list[dict]:
        with self.db.connection() as connection:
            clip = self._clip(connection, clip_id)
            return [
                {
                    **dict(row),
                    "source_asset_id": clip["source_asset_id"],
                    "source_sha256": clip["source_sha256"],
                }
                for row in connection.execute(
                    "SELECT * FROM notes WHERE clip_id=? ORDER BY at_seconds,created_at", (clip_id,)
                )
            ]

    def add_note(self, clip_id: str, body: str, at_seconds: float | None) -> dict:
        with self.db.connection(write=True) as connection:
            clip = self._clip(connection, clip_id)
            if at_seconds is not None and at_seconds > clip["source_duration"]:
                raise CatalogError(
                    "invalid_timestamp", "The note must fall within the original", 422
                )
            record = {
                "id": str(uuid4()),
                "clip_id": clip_id,
                "body": body,
                "at_seconds": at_seconds,
                "created_at": now(),
            }
            connection.execute(
                "INSERT INTO notes(id,clip_id,body,at_seconds,created_at) VALUES(?,?,?,?,?)",
                list(record.values()),
            )
            return {
                **record,
                "source_asset_id": clip["source_asset_id"],
                "source_sha256": clip["source_sha256"],
            }

    def delete_note(self, clip_id: str, note_id: str) -> None:
        with self.db.connection(write=True) as connection:
            self._require(connection, "clips", clip_id)
            count = connection.execute(
                "DELETE FROM notes WHERE id=? AND clip_id=?", (note_id, clip_id)
            ).rowcount
            if not count:
                raise CatalogError("note_not_found", "This note does not belong to this clip", 404)

    def playlist(self, playlist_id: str) -> dict:
        with self.db.connection() as connection:
            record = dict(self._require(connection, "playlists", playlist_id))
            record["clip_ids"] = [
                row[0]
                for row in connection.execute(
                    "SELECT clip_id FROM playlist_items WHERE playlist_id=? ORDER BY ordinal",
                    (playlist_id,),
                )
            ]
            record["clips"] = [self._clip(connection, item) for item in record["clip_ids"]]
            return record

    def set_playlist(self, playlist_id: str, clip_ids: list[str]) -> dict:
        with self.db.connection(write=True) as connection:
            self._require(connection, "playlists", playlist_id)
            for clip_id in clip_ids:
                self._require(connection, "clips", clip_id)
            connection.execute("DELETE FROM playlist_items WHERE playlist_id=?", (playlist_id,))
            connection.executemany(
                "INSERT INTO playlist_items(playlist_id,clip_id,ordinal) VALUES(?,?,?)",
                [(playlist_id, clip_id, ordinal) for ordinal, clip_id in enumerate(clip_ids)],
            )
        return self.playlist(playlist_id)

    def analyses(self, clip_id: str) -> list[dict]:
        with self.db.connection() as connection:
            self._require(connection, "clips", clip_id)
            records = []
            for row in connection.execute(
                "SELECT * FROM analyses WHERE clip_id=? ORDER BY created_at DESC", (clip_id,)
            ):
                record = dict(row)
                record["payload"] = json.loads(record["payload"])
                record["reviews"] = [
                    dict(review)
                    for review in connection.execute(
                        "SELECT status,created_at FROM analysis_reviews "
                        "WHERE analysis_id=? ORDER BY id",
                        (record["id"],),
                    )
                ]
                records.append(record)
            return records

    def add_analysis(self, clip_id: str, payload: dict) -> dict:
        timestamp, analysis_id = now(), str(uuid4())
        with self.db.connection(write=True) as connection:
            self._require(connection, "clips", clip_id)
            connection.execute(
                "INSERT INTO analyses(id,clip_id,payload,created_at,updated_at) VALUES(?,?,?,?,?)",
                (analysis_id, clip_id, encode(payload), timestamp, timestamp),
            )
        return next(item for item in self.analyses(clip_id) if item["id"] == analysis_id)

    def review_analysis(self, clip_id: str, analysis_id: str, status: str) -> dict:
        timestamp = now()
        with self.db.connection(write=True) as connection:
            self._require(connection, "clips", clip_id)
            changed = connection.execute(
                "UPDATE analyses SET status=?,updated_at=? WHERE id=? AND clip_id=?",
                (status, timestamp, analysis_id, clip_id),
            ).rowcount
            if not changed:
                raise CatalogError(
                    "analysis_not_found", "This result does not belong to this clip", 404
                )
            connection.execute(
                "INSERT INTO analysis_reviews(analysis_id,status,created_at) VALUES(?,?,?)",
                (analysis_id, status, timestamp),
            )
        return next(item for item in self.analyses(clip_id) if item["id"] == analysis_id)

    def sidecar(self, clip_id: str) -> dict | None:
        with self.db.connection() as connection:
            self._require(connection, "clips", clip_id)
            row = connection.execute(
                "SELECT * FROM sidecars WHERE clip_id=?", (clip_id,)
            ).fetchone()
            return dict(row) if row else None

    def save_sidecar(self, clip_id: str, content: bytes, summary: dict) -> dict:
        self.get_clip(clip_id)
        sha256 = hashlib.sha256(content).hexdigest()
        relative = f"reviews/{clip_id}/annotations/{sha256}.kva"
        path = self.settings.path(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f"{uuid4()}.tmp")
        try:
            temporary.write_bytes(content)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
        timestamp = now()
        with self.db.connection(write=True) as connection:
            connection.execute(
                """INSERT INTO sidecars(clip_id,path,sha256,summary,updated_at) VALUES(?,?,?,?,?)
                   ON CONFLICT(clip_id) DO UPDATE SET path=excluded.path,sha256=excluded.sha256,
                   summary=excluded.summary,updated_at=excluded.updated_at""",
                (clip_id, relative, sha256, encode(summary), timestamp),
            )
        return {
            "clip_id": clip_id,
            "sha256": sha256,
            "summary": summary,
            "updated_at": timestamp,
            "annotations_url": f"/api/clips/{clip_id}/kinovea/annotations",
        }
