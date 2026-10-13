"""A bounded local export worker with durable, frozen job inputs."""

from __future__ import annotations

import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

from .media import MediaError, export_trim
from .service import Catalog, CatalogError, encode, file_sha256, now

logger = logging.getLogger(__name__)


class ExportJobs:
    def __init__(self, catalog: Catalog):
        self.catalog = catalog
        self._lock = threading.Lock()
        self._executor: ThreadPoolExecutor | None = None

    def _fail_pending(self, code: str, message: str) -> None:
        with self.catalog.db.connection(write=True) as connection:
            connection.execute(
                "UPDATE jobs SET status='failed',error=?,updated_at=? "
                "WHERE status IN ('queued','running')",
                (encode({"code": code, "message": message}), now()),
            )

    def start(self) -> None:
        with self._lock:
            if self._executor is not None:
                return
            self._fail_pending(
                "interrupted", "The application stopped before this export completed"
            )
            self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="exais-export")

    def stop(self) -> None:
        with self._lock:
            executor, self._executor = self._executor, None
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=True)
            self._fail_pending("shutdown", "Export was queued when the application stopped")

    @staticmethod
    def _public(row) -> dict:
        record = dict(row)
        snapshot = json.loads(record.pop("snapshot"))
        record.pop("output_path")
        record["error"] = json.loads(record["error"]) if record["error"] else None
        record["download_url"] = (
            f"/api/jobs/{record['id']}/file" if record["status"] == "complete" else None
        )
        record["provenance"] = {
            "source_sha256": snapshot["source_sha256"],
            "source_asset_id": snapshot.get("source_asset_id"),
            "clip_id": record["clip_id"],
            "start_seconds": snapshot["start_seconds"],
            "end_seconds": snapshot["end_seconds"],
            "clip_version": record["clip_version"],
        }
        if "export" in snapshot:
            record["result"] = snapshot["export"]
        return record

    def get(self, job_id: str) -> dict:
        with self.catalog.db.connection() as connection:
            return self._public(self.catalog._require(connection, "jobs", job_id))

    def list(self) -> list[dict]:
        with self.catalog.db.connection() as connection:
            return [
                self._public(row)
                for row in connection.execute(
                    "SELECT * FROM jobs ORDER BY created_at DESC LIMIT 100"
                )
            ]

    def enqueue(self, clip_id: str) -> dict:
        with self._lock:
            if self._executor is None:
                raise CatalogError("worker_unavailable", "The export worker is not running", 503)
            with self.catalog.db.connection(write=True) as connection:
                clip = self.catalog._clip(connection, clip_id)
                count = connection.execute(
                    "SELECT count(*) FROM jobs WHERE status IN ('queued','running')"
                ).fetchone()[0]
                if count >= self.catalog.settings.max_pending_jobs:
                    raise CatalogError(
                        "queue_full", "Let a pending export finish before adding more", 429
                    )
                asset = connection.execute(
                    "SELECT a.path FROM assets a JOIN clips c ON c.asset_id=a.id WHERE c.id=?",
                    (clip_id,),
                ).fetchone()
                snapshot = {**clip, "source_path": asset[0]}
                job_id, timestamp = str(uuid4()), now()
                connection.execute(
                    """INSERT INTO jobs(id,clip_id,kind,status,clip_version,snapshot,created_at,
                       updated_at) VALUES(?,?,'trim','queued',?,?,?,?)""",
                    (job_id, clip_id, clip["version"], encode(snapshot), timestamp, timestamp),
                )
            self._executor.submit(self._run, job_id, snapshot)
        return self.get(job_id)

    def _run(self, job_id: str, snapshot: dict) -> None:
        relative = f"exports/{job_id}.mp4"
        output = self.catalog.settings.path(relative)
        try:
            with self.catalog.db.connection(write=True) as connection:
                connection.execute(
                    "UPDATE jobs SET status='running',updated_at=? WHERE id=?", (now(), job_id)
                )
            source = self.catalog.settings.path(snapshot["source_path"])
            if not source.is_file():
                raise MediaError("source_missing", "The original media file is missing")
            if file_sha256(source) != snapshot["source_sha256"]:
                raise MediaError(
                    "source_changed", "The original no longer matches its recorded hash"
                )
            result = export_trim(source, output, snapshot["start_seconds"], snapshot["end_seconds"])
            if result["source_sha256"] != snapshot["source_sha256"]:
                raise MediaError(
                    "source_changed", "The rendered source no longer matches this saved export job"
                )
            snapshot = {**snapshot, "export": result}
            with self.catalog.db.connection(write=True) as connection:
                connection.execute(
                    "UPDATE jobs SET status='complete',output_path=?,snapshot=?,updated_at=? "
                    "WHERE id=?",
                    (relative, encode(snapshot), now(), job_id),
                )
        except Exception as error:
            output.unlink(missing_ok=True)
            if isinstance(error, MediaError):
                detail = {"code": error.code, "message": error.message}
            else:
                logger.exception("Export job %s failed", job_id)
                detail = {
                    "code": "export_failed",
                    "message": "Export failed; inspect the local log",
                }
            with self.catalog.db.connection(write=True) as connection:
                connection.execute(
                    "UPDATE jobs SET status='failed',error=?,updated_at=? WHERE id=?",
                    (encode(detail), now(), job_id),
                )

    def file(self, job_id: str):
        with self.catalog.db.connection() as connection:
            record = self.catalog._require(connection, "jobs", job_id)
            if record["status"] != "complete":
                raise CatalogError("export_not_ready", "This export is not ready to download", 409)
            path = self.catalog.settings.path(record["output_path"])
            if not path.is_file():
                raise CatalogError("export_missing", "This exported file is missing", 404)
            return path
