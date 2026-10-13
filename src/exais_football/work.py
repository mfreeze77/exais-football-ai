"""Bounded, durable local work queue with immutable attempts and explicit interruption."""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from uuid import uuid4

from .service import CatalogError, encode, now

logger = logging.getLogger(__name__)
Handler = Callable[[dict, threading.Event, Callable[[str], None]], dict]


class WorkJobs:
    def __init__(self, catalog):
        self.catalog = catalog
        self.handlers: dict[str, Handler] = {}
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._guard = threading.RLock()
        self._events: dict[str, threading.Event] = {}
        self._thread: threading.Thread | None = None

    def register(self, kind: str, handler: Handler):
        if self._thread is not None:
            raise RuntimeError("Register handlers before starting workers")
        self.handlers[kind] = handler

    @staticmethod
    def _public(row):
        result = dict(row)
        for key in ("snapshot", "result", "error"):
            result[key] = json.loads(result[key]) if result[key] is not None else None
        # Operator paths only belong in a private execution snapshot, not the UI.
        if result["snapshot"]:
            result["snapshot"] = {
                k: v for k, v in result["snapshot"].items() if not k.startswith("_")
            }
        return result

    def get(self, job_id: str):
        with self.catalog.db.connection() as db:
            row = db.execute("SELECT * FROM work_jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                raise CatalogError("work_not_found", "This task does not exist", 404)
            return self._public(row)

    def list(self):
        with self.catalog.db.connection() as db:
            return [
                self._public(r)
                for r in db.execute("SELECT * FROM work_jobs ORDER BY created_at DESC LIMIT 100")
            ]

    def enqueue(
        self,
        kind: str,
        snapshot: dict,
        clip_id: str | None = None,
        *,
        parent_id: str | None = None,
        attempt: int = 1,
    ):
        if kind not in self.handlers:
            raise CatalogError("work_kind", "This task type is not enabled", 422)
        job_id, timestamp = str(uuid4()), now()
        with self.catalog.db.connection(write=True) as db:
            if (
                db.execute(
                    "SELECT count(*) FROM work_jobs WHERE status IN ('queued','running')"
                ).fetchone()[0]
                >= self.catalog.settings.max_pending_jobs
            ):
                raise CatalogError(
                    "queue_full", "The local work queue is full; finish or cancel another task", 429
                )
            if clip_id is not None:
                self.catalog._require(db, "clips", clip_id)
            db.execute(
                (
                    "INSERT INTO "
                    "work_jobs(id,kind,clip_id,status,snapshot,attempt"
                    ",parent_id,created_at,updated_at) VALUES(?,?,?,'queued',?,?,?,?,?)"
                ),
                (job_id, kind, clip_id, encode(snapshot), attempt, parent_id, timestamp, timestamp),
            )
        self._wake.set()
        return self.get(job_id)

    def retry(self, job_id):
        with self.catalog.db.connection() as db:
            row = db.execute("SELECT * FROM work_jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                raise CatalogError("work_not_found", "This task does not exist", 404)
            if row["status"] not in {"failed", "cancelled", "interrupted"}:
                raise CatalogError("retry_state", "Only unsuccessful attempts may be retried", 409)
            snapshot = json.loads(row["snapshot"])
        return self.enqueue(
            row["kind"], snapshot, row["clip_id"], parent_id=job_id, attempt=row["attempt"] + 1
        )

    def cancel(self, job_id):
        with self._guard, self.catalog.db.connection(write=True) as db:
            row = db.execute("SELECT status FROM work_jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                raise CatalogError("work_not_found", "This task does not exist", 404)
            if row["status"] == "queued":
                db.execute(
                    (
                        "UPDATE work_jobs SET "
                        "status='cancelled',stage='cancelled',updated_at=? WHERE id=?"
                    ),
                    (now(), job_id),
                )
            elif row["status"] == "running":
                event = self._events.get(job_id)
                if event is not None:
                    event.set()
                db.execute(
                    "UPDATE work_jobs SET stage='cancellation_requested',updated_at=? WHERE id=?",
                    (now(), job_id),
                )
        return self.get(job_id)

    def start(self):
        with self._guard:
            if self._thread is not None:
                return
            # Do not silently resume either queued or running historical intent.
            with self.catalog.db.connection(write=True) as db:
                db.execute(
                    (
                        "UPDATE work_jobs SET "
                        "status='interrupted',stage='interrupted',erro"
                        "r=?,updated_at=? WHERE status IN ('running','queued')"
                    ),
                    (
                        encode(
                            {
                                "code": "interrupted",
                                "message": (
                                    "The application stopped before completion; retry deliberately"
                                ),
                            }
                        ),
                        now(),
                    ),
                )
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, name="exais-local-work", daemon=True)
            self._thread.start()

    def stop(self):
        with self._guard:
            self._stop.set()
            self._wake.set()
            for event in self._events.values():
                event.set()
            thread = self._thread
        if thread is not None:
            # Cancellation-aware handlers terminate their bounded subprocesses.
            # Keep catalog ownership until the worker actually stops; never release
            # the instance lock while an old worker can still publish files.
            thread.join()
        with self._guard:
            if thread is not None and thread.is_alive():
                logger.error(
                    "A local task did not acknowledge shutdown; "
                    "its next startup status is interrupted"
                )
            self._thread = None

    def _loop(self):
        while not self._stop.is_set():
            try:
                idle = not self.run_once()
            except Exception:
                # A catalog I/O failure must not silently kill the durable worker.
                # In-flight attempts remain visible; next startup marks interruptions.
                logger.exception("The local work catalog is unavailable")
                self._stop.wait(1)
                continue
            if idle:
                self._wake.wait(0.25)
                self._wake.clear()

    def run_once(self) -> bool:
        """One deterministic worker iteration, also usable in regression tests."""
        with self._guard, self.catalog.db.connection(write=True) as db:
            row = db.execute(
                "SELECT * FROM work_jobs WHERE status='queued' ORDER BY created_at,id LIMIT 1"
            ).fetchone()
            if row is None:
                return False
            job_id = row["id"]
            cancel = threading.Event()
            self._events[job_id] = cancel
            db.execute(
                "UPDATE work_jobs SET status='running',stage='starting',updated_at=? WHERE id=?",
                (now(), job_id),
            )

        def stage(name):
            if cancel.is_set():
                raise CatalogError("job_cancelled", "This task was cancelled", 409)
            with self.catalog.db.connection(write=True) as db:
                db.execute(
                    "UPDATE work_jobs SET stage=?,updated_at=? WHERE id=?",
                    (name[:120], now(), job_id),
                )

        try:
            stage("preflight")
            handler = self.handlers.get(row["kind"])
            if handler is None:
                raise CatalogError("work_unavailable", "This task type is no longer enabled", 503)
            result = handler(json.loads(row["snapshot"]), cancel, stage)
            # Successful publication is the commit point. A late cancellation does
            # not falsely claim a committed artifact was discarded.
            status, error = "complete", None
        except Exception as exc:
            result = None
            code = exc.code if isinstance(exc, CatalogError) else "work_failed"
            message = (
                exc.message
                if isinstance(exc, CatalogError)
                else (
                    "Local processing failed. Check available "
                    "storage, tools, and permissions; no source was deleted."
                )
            )
            status = "cancelled" if code == "job_cancelled" else "failed"
            error = {"code": code, "message": message}
            logger.info("Task %s failed with %s", job_id, code)
        finally:
            with self._guard:
                self._events.pop(job_id, None)
        with self.catalog.db.connection(write=True) as db:
            db.execute(
                "UPDATE work_jobs SET status=?,stage=?,result=?,error=?,updated_at=? WHERE id=?",
                (
                    status,
                    status,
                    encode(result) if result is not None else None,
                    encode(error) if error else None,
                    now(),
                    job_id,
                ),
            )
        return True


def register_recovery(jobs):
    from .portable import create_package
    from .recovery import create_backup, inspect_workspace

    jobs.register("backup", lambda snapshot, cancel, stage: create_backup(jobs.catalog, cancel))
    jobs.register(
        "package", lambda snapshot, cancel, stage: create_package(jobs.catalog, snapshot, cancel)
    )
    jobs.register(
        "integrity",
        lambda snapshot, cancel, stage: inspect_workspace(jobs.catalog, verify_hashes=True),
    )
