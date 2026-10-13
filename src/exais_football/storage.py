"""Read-only storage reporting and narrowly allowlisted, previewed cache cleanup."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import threading
from pathlib import Path

from fastapi.responses import FileResponse

from .service import CatalogError
from .study import token
from .workspace import managed_path, workspace_lock

_LEASES: dict[str, int] = {}
_LEASE_LOCK = threading.Lock()


class LeasedFileResponse(FileResponse):
    """Keep managed cleanup from unlinking files between response creation and send."""

    def __init__(self, path, **kwargs):
        super().__init__(path, **kwargs)
        self._lease = str(Path(path).resolve())
        with _LEASE_LOCK:
            _LEASES[self._lease] = _LEASES.get(self._lease, 0) + 1

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            with _LEASE_LOCK:
                _LEASES[self._lease] -= 1
                if not _LEASES[self._lease]:
                    del _LEASES[self._lease]


def leased(path: Path) -> bool:
    with _LEASE_LOCK:
        return bool(_LEASES.get(str(path.resolve())))


def report(catalog) -> dict:
    totals = {
        k: 0
        for k in (
            "sources",
            "thumbnails",
            "exports",
            "reviews",
            "cache",
            "analysis",
            "artifacts",
            "staging",
            "backups",
            "packages",
        )
    }
    candidates = []
    with catalog.db.connection() as db:
        exports = {
            row["output_path"]: row["id"]
            for row in db.execute(
                "SELECT id,output_path FROM jobs WHERE "
                "status='complete' AND output_path IS NOT NULL"
            )
        }
        busy = (
            db.execute("SELECT count(*) FROM jobs WHERE status IN ('running','queued')").fetchone()[
                0
            ]
            + db.execute(
                "SELECT count(*) FROM work_jobs WHERE status IN "
                "('running','queued') AND kind!='cleanup'"
            ).fetchone()[0]
        )
    for kind in totals:
        for path in catalog.settings.path(kind).rglob("*"):
            if not path.is_file() or path.is_symlink():
                continue
            relative = path.relative_to(catalog.settings.data_dir).as_posix()
            try:
                managed_path(catalog.settings.data_dir, relative)
                info = path.stat()
            except (OSError, CatalogError):
                continue
            totals[kind] += info.st_size
            eligible = (
                kind == "thumbnails"
                and re.fullmatch(r"thumbnails/[a-f0-9-]{36}\.jpg", relative)
                or kind == "cache"
                and re.fullmatch(r"cache/frames/[a-f0-9]{64}/\d{6}\.png", relative)
                or kind == "exports"
                and relative in exports
            )
            if eligible:
                candidates.append(
                    {
                        "id": hashlib.sha256(relative.encode()).hexdigest(),
                        "path": relative,
                        "bytes": info.st_size,
                        "mtime_ns": info.st_mtime_ns,
                        "kind": kind,
                        "regenerable": True,
                        "eligible": not busy and not leased(path),
                        "excluded_reason": "jobs_active"
                        if busy
                        else "active_download"
                        if leased(path)
                        else None,
                    }
                )
    return {
        "managed_root": str(catalog.settings.data_dir),
        "totals": totals,
        "total_bytes": sum(totals.values()),
        "free_bytes": shutil.disk_usage(catalog.settings.data_dir).free,
        "candidates": candidates,
        "policy": (
            "Only thumbnails, decoded-frame PNG caches, and retained "
            "completed trim exports. Original film, KVA, analyses, "
            "overlays, snapshots, manifests, and backups are never cleanup candidates."
        ),
    }


def preview_cleanup(catalog, ids: list[str]) -> dict:
    if not 1 <= len(ids) <= 1000 or len(set(ids)) != len(ids):
        raise CatalogError("cleanup_selection", "Select 1–1000 distinct cache entries", 422)
    inventory = report(catalog)
    candidates = {c["id"]: c for c in inventory["candidates"]}
    if any(i not in candidates or not candidates[i]["eligible"] for i in ids):
        raise CatalogError(
            "cleanup_ineligible",
            "Selection contains a protected, missing, or in-use file; refresh storage",
            409,
        )
    files = [candidates[i] for i in ids]
    data = {"files": files, "total_bytes": sum(f["bytes"] for f in files)}
    return {**data, "preview_token": token(data)}


def cleanup(catalog, ids: list[str], preview_token: str) -> dict:
    with workspace_lock(catalog):
        plan = preview_cleanup(catalog, ids)
        if plan["preview_token"] != preview_token:
            raise CatalogError("preview_stale", "Storage changed; preview cleanup again", 409)
        removed = []
        failed = []
        for item in plan["files"]:
            path = managed_path(catalog.settings.data_dir, item["path"])
            if leased(path):
                failed.append({"path": item["path"], "reason": "active_download"})
                continue
            try:
                path.unlink()
                removed.append(item)
            except OSError as exc:
                failed.append({"path": item["path"], "reason": type(exc).__name__})
        return {
            "removed": removed,
            "failed": failed,
            "recovered_bytes": sum(i["bytes"] for i in removed),
            "complete": not failed,
        }


def artifacts(catalog, clip_id: str | None = None) -> list[dict]:
    with catalog.db.connection() as db:
        rows = db.execute(
            "SELECT * FROM artifact_records"
            + (" WHERE clip_id=?" if clip_id else "")
            + " ORDER BY created_at DESC LIMIT 100",
            (clip_id,) if clip_id else (),
        ).fetchall()
        return [
            {
                "id": r["id"],
                "kind": r["kind"],
                "clip_id": r["clip_id"],
                "stale": bool(r["stale"]),
                "created_at": r["created_at"],
                "manifest": json.loads(r["manifest"]),
                "download_url": f"/api/artifacts/{r['id']}/file"
                if catalog.settings.path(r["path"]).is_file()
                else None,
            }
            for r in rows
        ]
