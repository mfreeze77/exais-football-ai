"""Verified full-library backups and create-only, staged restoration.

All restore destinations are new workspaces. No operation overwrites a live
catalog, follows archive symlinks, executes archive members, or deletes originals.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import threading
import time
import unicodedata
import zipfile
from contextlib import closing
from pathlib import Path
from uuid import uuid4

from .db import SCHEMA, SCHEMA_VERSION
from .evolution import migrate_v3
from .service import CatalogError, encode, file_sha256, now
from .tagging import migrate_v2
from .workspace import managed_path, register_artifact, workspace_lock

MAX_MEMBERS = 100_000
MAX_RESTORE_BYTES = 100 * 1024**3
MAX_MEMBER_BYTES = 8 * 1024**3
COPY_BLOCK = 1024 * 1024
BACKUP_FORMAT = "exais-full-library"


def _cancel(cancel: threading.Event | None):
    if cancel is not None and cancel.is_set():
        raise CatalogError("job_cancelled", "The owner cancelled this operation", 409)


def _stream_copy(source, destination, *, expected_size: int, cancel=None) -> str:
    digest = hashlib.sha256()
    count = 0
    while True:
        _cancel(cancel)
        chunk = source.read(COPY_BLOCK)
        if not chunk:
            break
        count += len(chunk)
        if count > expected_size:
            raise CatalogError("size_mismatch", "File expanded beyond its declared size", 422)
        digest.update(chunk)
        if destination is not None:
            destination.write(chunk)
    if count != expected_size:
        raise CatalogError("size_mismatch", "A file is truncated or changed during copying", 422)
    return digest.hexdigest()


def snapshot_database(catalog, destination: Path) -> None:
    """The caller holds the coordination lock; SQLite backup includes committed WAL."""
    with (
        catalog.db.connection(write=True),
        closing(sqlite3.connect(catalog.db.path)) as source,
        closing(sqlite3.connect(destination)) as target,
    ):
        source.backup(target)
        if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise CatalogError(
                "backup_integrity", "Catalog snapshot failed SQLite integrity verification", 409
            )


def _backup_inventory(database: Path, catalog) -> tuple[list[dict], dict]:
    entries = [
        {
            "path": "catalog.sqlite3",
            "source": database,
            "kind": "catalog",
            "expected_sha256": file_sha256(database),
        }
    ]
    seen = {"catalog.sqlite3"}

    def add(relative: str, kind: str, expected: str | None = None):
        if relative in seen:
            return
        source = managed_path(catalog.settings.data_dir, relative)
        entries.append(
            {"path": relative, "source": source, "kind": kind, "expected_sha256": expected}
        )
        seen.add(relative)

    with closing(sqlite3.connect(database)) as db:
        db.row_factory = sqlite3.Row
        for row in db.execute("SELECT path,sha256 FROM assets"):
            add(row["path"], "original", row["sha256"])
        for row in db.execute("SELECT path,sha256 FROM sidecars"):
            add(row["path"], "sidecar", row["sha256"])
        for row in db.execute(
            "SELECT output_path,snapshot FROM jobs WHERE "
            "status='complete' AND output_path IS NOT NULL"
        ):
            # An intentionally reclaimed export is optional; its frozen intent stays in DB.
            path = catalog.settings.path(row["output_path"])
            if path.exists():
                add(
                    row["output_path"],
                    "trim_export",
                    json.loads(row["snapshot"]).get("export", {}).get("output_sha256"),
                )
        if SCHEMA_VERSION >= 3:
            for row in db.execute(
                "SELECT path,sha256,kind FROM artifact_records "
                "WHERE kind NOT IN ('full_backup','portable_package')"
            ):
                add(row["path"], row["kind"], row["sha256"])
            for row in db.execute("SELECT directory,manifest FROM cv_runs"):
                manifest = json.loads(row["manifest"])
                for item in manifest["files"]:
                    add(row["directory"] + "/" + item["path"], "analysis", item["sha256"])
        in_flight = {
            "exports": [
                dict(r)
                for r in db.execute(
                    "SELECT id,status,kind FROM jobs WHERE status IN ('queued','running')"
                )
            ],
            "work": [
                dict(r)
                for r in db.execute(
                    "SELECT id,status,kind FROM work_jobs WHERE status IN ('queued','running')"
                )
            ],
        }
    # Preserve every imported immutable KVA revision, not the external app's mutable working copy.
    for path in catalog.settings.path("reviews").glob("*/annotations/*.kva"):
        add(path.relative_to(catalog.settings.data_dir).as_posix(), "sidecar_history")
    return entries, in_flight


def create_backup(catalog, cancel=None) -> dict:
    backup_id = str(uuid4())
    destination = catalog.settings.path(f"backups/{backup_id}.efabackup.zip")
    temporary = destination.with_name(destination.name + ".partial")
    database = catalog.settings.path(f"staging/{backup_id}.sqlite3")
    with workspace_lock(catalog):
        try:
            snapshot_database(catalog, database)
            entries, in_flight = _backup_inventory(database, catalog)
            total = sum(e["source"].stat().st_size for e in entries)
            if shutil.disk_usage(destination.parent).free < total + 16 * 1024**2:
                raise CatalogError(
                    "backup_space",
                    "Not enough free space for a complete backup; no film was deleted",
                    507,
                )
            manifest = {
                "schema_version": 1,
                "kind": BACKUP_FORMAT,
                "backup_id": backup_id,
                "created_at": now(),
                "catalog_schema": SCHEMA_VERSION,
                "files": [],
                "in_flight_jobs": in_flight,
                "policy": (
                    "Committed catalog and required immutable files; "
                    "caches and native working copies excluded. "
                    "In-flight outputs are not successful artifacts "
                    "and restore as interrupted."
                ),
            }
            with zipfile.ZipFile(
                temporary, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=1, allowZip64=True
            ) as archive:
                for item in entries:
                    _cancel(cancel)
                    size = item["source"].stat().st_size
                    with (
                        item["source"].open("rb") as source,
                        archive.open(item["path"], "w", force_zip64=True) as target,
                    ):
                        digest = _stream_copy(source, target, expected_size=size, cancel=cancel)
                    if item["expected_sha256"] and digest != item["expected_sha256"]:
                        raise CatalogError(
                            "backup_source_changed",
                            f"A required file failed checksum verification: {item['path']}",
                            409,
                        )
                    manifest["files"].append(
                        {"path": item["path"], "sha256": digest, "size": size, "kind": item["kind"]}
                    )
                archive.writestr("manifest.json", encode(manifest))
            _cancel(cancel)
            # Validate the finished archive, not just the input files.
            inspect_package(temporary, expected_kind=BACKUP_FORMAT, cancel=cancel)
            temporary.replace(destination)
            try:
                return register_artifact(catalog, "full_backup", None, destination, manifest)
            except BaseException:
                destination.unlink(missing_ok=True)
                raise
        finally:
            database.unlink(missing_ok=True)
            temporary.unlink(missing_ok=True)


def inspect_package(
    path: Path,
    *,
    expected_kind: str | None = None,
    cancel=None,
    max_total_bytes: int = MAX_RESTORE_BYTES,
) -> dict:
    """Verify every member before extraction/activation. Never trust ZIP metadata alone."""
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            if not 2 <= len(infos) <= MAX_MEMBERS:
                raise CatalogError(
                    "archive_limits", "Archive member count is outside supported limits", 422
                )
            names = set()
            folded = set()
            total = 0
            for info in infos:
                _cancel(cancel)
                managed_path(path.parent, info.filename, must_exist=False)
                mode = info.external_attr >> 16
                if (
                    info.is_dir()
                    or stat.S_ISLNK(mode)
                    or (stat.S_IFMT(mode) not in {0, stat.S_IFREG})
                ):
                    raise CatalogError(
                        "archive_member_type",
                        "Archive directories, links, and special files are not allowed",
                        422,
                    )
                if (
                    info.filename in names
                    or unicodedata.normalize("NFC", info.filename).casefold() in folded
                ):
                    raise CatalogError(
                        "archive_duplicate",
                        "Archive contains duplicate or case-colliding paths",
                        422,
                    )
                if info.flag_bits & 1:
                    raise CatalogError(
                        "archive_encrypted", "Encrypted archives are not supported", 422
                    )
                if info.file_size > MAX_MEMBER_BYTES or info.file_size < 0:
                    raise CatalogError("archive_limits", "Archive member is too large", 422)
                total += info.file_size
                if total > max_total_bytes:
                    raise CatalogError(
                        "archive_limits", "Archive exceeds the total restore size limit", 422
                    )
                names.add(info.filename)
                folded.add(unicodedata.normalize("NFC", info.filename).casefold())
            if (
                "manifest.json" not in names
                or archive.getinfo("manifest.json").file_size > 8 * 1024**2
            ):
                raise CatalogError(
                    "manifest_missing", "A bounded package manifest is required", 422
                )
            manifest = json.loads(archive.read("manifest.json"))
            if (
                not isinstance(manifest, dict)
                or manifest.get("schema_version") != 1
                or manifest.get("kind")
                not in {BACKUP_FORMAT, "exais-selected-film", "exais-football-analysis"}
                or expected_kind is not None
                and manifest.get("kind") != expected_kind
            ):
                raise CatalogError(
                    "manifest_schema", "Unsupported package kind or schema version", 422
                )
            files = manifest.get("files")
            if not isinstance(files, list) or len(files) != len(names) - 1:
                raise CatalogError(
                    "manifest_members",
                    "Every archived file must appear exactly once in the manifest",
                    422,
                )
            entries = {}
            for item in files:
                if (
                    not isinstance(item, dict)
                    or not isinstance(item.get("path"), str)
                    or not re.fullmatch(r"[a-f0-9]{64}", str(item.get("sha256", "")))
                    or type(item.get("size")) is not int
                    or item["size"] < 0
                ):
                    raise CatalogError("manifest_schema", "Invalid file manifest entry", 422)
                if item["path"] in entries or item["path"] == "manifest.json":
                    raise CatalogError(
                        "manifest_members", "Manifest contains duplicate entries", 422
                    )
                entries[item["path"]] = item
            if set(entries) != names - {"manifest.json"}:
                raise CatalogError(
                    "manifest_members", "Package contains missing or unlisted files", 422
                )
            if manifest["kind"] == BACKUP_FORMAT and "catalog.sqlite3" not in entries:
                raise CatalogError("manifest_catalog", "Full backup has no catalog snapshot", 422)
            for name, item in entries.items():
                info = archive.getinfo(name)
                if info.file_size != item["size"]:
                    raise CatalogError(
                        "size_mismatch", "Archived size does not match its manifest", 422
                    )
                with archive.open(name) as source:
                    digest = _stream_copy(source, None, expected_size=item["size"], cancel=cancel)
                if digest != item["sha256"]:
                    raise CatalogError("checksum_mismatch", f"Checksum mismatch for {name}", 422)
            return {
                "status": "verified",
                "archive_sha256": file_sha256(path),
                "total_bytes": total,
                "file_count": len(files),
                "manifest": manifest,
            }
    except (zipfile.BadZipFile, KeyError, ValueError, UnicodeError, RuntimeError) as exc:
        if isinstance(exc, CatalogError):
            raise
        raise CatalogError(
            "invalid_archive", "This is not a valid, intact EXAIS package", 422
        ) from exc


def verify_catalog(database: Path) -> int:
    """Reject unknown schema, triggers and views before opening a restored DB normally."""
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("PRAGMA trusted_schema=OFF")
        deadline = time.monotonic() + 30
        db.set_progress_handler(lambda: int(time.monotonic() > deadline), 10_000)
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if version not in {1, 2, 3}:
            raise CatalogError(
                "schema_unsupported", "Use a compatible application for this catalog schema", 409
            )
        if (
            db.execute("PRAGMA quick_check").fetchone()[0] != "ok"
            or db.execute("PRAGMA foreign_key_check").fetchall()
        ):
            raise CatalogError(
                "catalog_corrupt",
                "The restored catalog failed integrity or relationship checks",
                422,
            )
        with closing(sqlite3.connect(":memory:")) as reference:
            reference.executescript(SCHEMA)
            if version >= 2:
                migrate_v2(reference)
            if version >= 3:
                migrate_v3(reference)

            def structure(connection):
                return {
                    (r[0], r[1]): re.sub(r"\s+", " ", r[2] or "").strip().casefold()
                    for r in connection.execute(
                        "SELECT type,name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
                    )
                }

            if structure(db) != structure(reference):
                raise CatalogError(
                    "catalog_schema_changed",
                    (
                        "The backup contains an unexpected database "
                        "definition; no restore was activated"
                    ),
                    422,
                )
        return version


def restore_backup(
    archive_path: Path,
    destination: Path,
    *,
    dry_run: bool = True,
    expected_archive_sha256: str | None = None,
    cancel=None,
) -> dict:
    report = inspect_package(archive_path, expected_kind=BACKUP_FORMAT, cancel=cancel)
    if expected_archive_sha256 and report["archive_sha256"] != expected_archive_sha256:
        raise CatalogError("restore_preview_stale", "The archive changed after preview", 409)
    destination = destination.expanduser().absolute()
    if destination.exists():
        raise CatalogError(
            "restore_destination_exists",
            "Choose a new nonexistent workspace; existing libraries are never overwritten",
            409,
        )
    if destination.is_symlink() or any(p.is_symlink() for p in destination.parents):
        raise CatalogError(
            "unsafe_symlink", "The restore destination must not traverse symlinks", 422
        )
    if dry_run:
        return {
            **report,
            "destination": str(destination),
            "activated": False,
            "next_action": (
                "Review this report, then run restore with --apply into this new workspace"
            ),
        }
    destination.parent.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(destination.parent).free < report["total_bytes"] + 16 * 1024**2:
        raise CatalogError(
            "restore_space", "Not enough disk space for the complete new workspace", 507
        )
    staging = destination.with_name(destination.name + f".restore-{uuid4()}.partial")
    staging.mkdir(mode=0o700, exist_ok=False)
    try:
        with zipfile.ZipFile(archive_path) as archive:
            for item in report["manifest"]["files"]:
                _cancel(cancel)
                target = managed_path(staging, item["path"], must_exist=False)
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(item["path"]) as source, target.open("xb") as output:
                    digest = _stream_copy(source, output, expected_size=item["size"], cancel=cancel)
                    output.flush()
                    os.fsync(output.fileno())
                if digest != item["sha256"]:
                    raise CatalogError(
                        "checksum_mismatch", "Archive changed during restoration", 422
                    )
        restored_version = verify_catalog(staging / "catalog.sqlite3")
        if report["manifest"].get("catalog_schema") != restored_version:
            raise CatalogError(
                "schema_mismatch", "Manifest and restored database schema differ", 422
            )
        # Verify each source/sidecar/run reference, not only archive membership.
        with closing(sqlite3.connect(staging / "catalog.sqlite3")) as db:
            for table in ("assets", "sidecars"):
                for relative, digest in db.execute(f"SELECT path,sha256 FROM {table}"):
                    if file_sha256(managed_path(staging, relative)) != digest:
                        raise CatalogError(
                            "restore_reference_mismatch",
                            "A catalog source reference failed verification",
                            422,
                        )
            if restored_version >= 3:
                for relative, digest in db.execute(
                    "SELECT path,sha256 FROM artifact_records "
                    "WHERE kind NOT IN ('full_backup','portable_package')"
                ):
                    if file_sha256(managed_path(staging, relative)) != digest:
                        raise CatalogError(
                            "restore_reference_mismatch",
                            "A retained artifact reference failed verification",
                            422,
                        )
                for directory, manifest_text in db.execute(
                    "SELECT directory,manifest FROM cv_runs"
                ):
                    for item in json.loads(manifest_text)["files"]:
                        if (
                            file_sha256(managed_path(staging, directory + "/" + item["path"]))
                            != item["sha256"]
                        ):
                            raise CatalogError(
                                "restore_reference_mismatch",
                                "An analysis reference failed verification",
                                422,
                            )
            db.execute(
                (
                    "UPDATE jobs SET "
                    "status='failed',error=?,updated_at=? WHERE "
                    "status IN ('queued','running')"
                ),
                (
                    encode(
                        {
                            "code": "interrupted_restore",
                            "message": (
                                "This export was in flight when the "
                                "backup was taken; retry deliberately"
                            ),
                        }
                    ),
                    now(),
                ),
            )
            if report["manifest"].get("catalog_schema", 0) >= 3:
                db.execute(
                    (
                        "UPDATE work_jobs SET "
                        "status='interrupted',error=?,updated_at=? "
                        "WHERE status IN ('queued','running')"
                    ),
                    (
                        encode(
                            {
                                "code": "interrupted_restore",
                                "message": "This task was not complete in the backup",
                            }
                        ),
                        now(),
                    ),
                )
            db.commit()
        # Recheck TOCTOU and never replace a destination created while verifying.
        _cancel(cancel)
        if file_sha256(archive_path) != report["archive_sha256"]:
            raise CatalogError(
                "restore_preview_stale", "The archive changed during restoration", 409
            )
        # Reserve the destination atomically. A POSIX directory rename may overwrite
        # an empty directory created between a check and rename, so do not use it.
        try:
            destination.mkdir(mode=0o700, exist_ok=False)
        except FileExistsError as exc:
            raise CatalogError(
                "restore_destination_exists",
                "Another process created the destination; it was not changed",
                409,
            ) from exc
        marker = destination / ".restore-in-progress"
        marker.write_text(
            encode({"archive_sha256": report["archive_sha256"], "staging": staging.name}),
            encoding="utf-8",
        )
        # Startup refuses this marker. A crash leaves an explicit incomplete restore,
        # never a partially activated library. All bytes were verified in staging.
        for child in staging.iterdir():
            _cancel(cancel)
            child.rename(destination / child.name)
        marker.unlink()

        return {
            "activated": True,
            "destination": str(destination),
            "archive_sha256": report["archive_sha256"],
            "file_count": report["file_count"],
            "policy": report["manifest"]["policy"],
        }
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def inspect_workspace(catalog, *, verify_hashes: bool = False) -> dict:
    missing = []
    changed = []
    partials = []
    orphans = []
    with catalog.db.connection() as db:
        assets = [dict(r) for r in db.execute("SELECT id,path,sha256 FROM assets")]
        running = [
            dict(r)
            for r in db.execute(
                "SELECT id,status FROM work_jobs WHERE status IN ('running','queued')"
            )
        ]
    expected = {a["path"] for a in assets}
    for asset in assets:
        try:
            path = managed_path(catalog.settings.data_dir, asset["path"])
            if verify_hashes and file_sha256(path) != asset["sha256"]:
                changed.append(asset["id"])
        except CatalogError:
            missing.append({"asset_id": asset["id"], "path": asset["path"]})
    for root in ("staging", "cache", "artifacts", "analysis", "packages", "backups"):
        for path in catalog.settings.path(root).rglob("*"):
            if path.is_file() and path.name.endswith((".partial", ".tmp")):
                partials.append(path.relative_to(catalog.settings.data_dir).as_posix())
                if len(partials) >= 1000:
                    break
    for path in catalog.settings.path("sources").rglob("*"):
        if (
            path.is_file()
            and path.relative_to(catalog.settings.data_dir).as_posix() not in expected
        ):
            orphans.append(path.relative_to(catalog.settings.data_dir).as_posix())
            if len(orphans) >= 1000:
                break
    return {
        "status": "degraded" if missing or changed else "ok",
        "source_count": len(assets),
        "missing_sources": missing,
        "changed_sources": changed,
        "partial_outputs": partials,
        "unreferenced_sources_preserved": orphans,
        "pending_work": running,
        "hashes_checked": verify_hashes,
        "automatic_deletion": False,
    }
