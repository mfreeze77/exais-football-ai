"""Managed-path, artifact-publication and bounded-process primitives."""

from __future__ import annotations

import hashlib
import json
import os
import re
import signal
import struct
import subprocess
import threading
import time
import zlib
from pathlib import Path, PurePosixPath
from uuid import uuid4

from .service import CatalogError, encode, file_sha256, now

MAX_PNG_BYTES = 9 * 1024 * 1024
_LOCKS: dict[str, threading.RLock] = {}
_LOCK_GUARD = threading.Lock()


def workspace_lock(catalog) -> threading.RLock:
    with _LOCK_GUARD:
        return _LOCKS.setdefault(str(catalog.settings.data_dir), threading.RLock())


def managed_path(root: Path, relative: str, *, must_exist: bool = True) -> Path:
    """Reject lexical traversal and every symlink, including in intermediate dirs."""
    pure = PurePosixPath(relative)
    if (
        not relative
        or "\\" in relative
        or ":" in relative
        or "\x00" in relative
        or pure.is_absolute()
        or any(
            not p
            or p in {"..", "."}
            or p.endswith((".", " "))
            or any(ord(c) < 32 for c in p)
            or re.fullmatch(r"(?i)(con|prn|aux|nul|com[0-9]|lpt[0-9])(?:\..*)?", p)
            for p in relative.split("/")
        )
    ):
        raise CatalogError("unsafe_path", "A file path escapes the managed workspace", 422)
    root = root.resolve()
    candidate = root
    for part in pure.parts:
        candidate = candidate / part
        if candidate.is_symlink():
            raise CatalogError("unsafe_symlink", "Symlinks are not accepted in film packages", 422)
    if not candidate.resolve().is_relative_to(root):
        raise CatalogError("unsafe_path", "A file path escapes the managed workspace", 422)
    if must_exist and not candidate.is_file():
        raise CatalogError("artifact_missing", f"Managed file is missing: {relative}", 404)
    return candidate


def atomic_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{uuid4()}.partial")
    try:
        with temporary.open("xb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        # This only publishes a brand-new UUID-named artifact. Never overwrite.
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def publish_artifact(
    catalog, kind: str, clip_id: str | None, content: bytes, suffix: str, manifest: dict
) -> dict:
    if suffix not in {".png", ".json", ".zip", ".csv", ".mp4"}:
        raise ValueError("Unsupported artifact suffix")
    artifact_id = str(uuid4())
    relative = f"artifacts/{artifact_id}{suffix}"
    path = managed_path(catalog.settings.data_dir, relative, must_exist=False)
    digest = hashlib.sha256(content).hexdigest()
    manifest = {
        **manifest,
        "artifact_id": artifact_id,
        "artifact_sha256": digest,
        "kind": kind,
        "created_at": now(),
    }
    with workspace_lock(catalog):
        try:
            atomic_bytes(path, content)
            with catalog.db.connection(write=True) as connection:
                if clip_id is not None:
                    catalog._require(connection, "clips", clip_id)
                connection.execute(
                    (
                        "INSERT INTO "
                        "artifact_records(id,kind,clip_id,path,sha256,"
                        "manifest,created_at) VALUES(?,?,?,?,?,?,?)"
                    ),
                    (artifact_id, kind, clip_id, relative, digest, encode(manifest), now()),
                )
        except BaseException:
            path.unlink(missing_ok=True)
            raise
    return {
        "id": artifact_id,
        "kind": kind,
        "manifest": manifest,
        "download_url": f"/api/artifacts/{artifact_id}/file",
        "stale": False,
    }


def register_artifact(catalog, kind: str, clip_id: str | None, path: Path, manifest: dict) -> dict:
    """Register an already atomically published, UUID-named large media artifact."""
    relative = path.relative_to(catalog.settings.data_dir).as_posix()
    managed_path(catalog.settings.data_dir, relative)
    artifact_id = str(uuid4())
    digest = file_sha256(path)
    manifest = {
        **manifest,
        "artifact_id": artifact_id,
        "artifact_sha256": digest,
        "kind": kind,
        "created_at": now(),
    }
    with catalog.db.connection(write=True) as connection:
        connection.execute(
            (
                "INSERT INTO "
                "artifact_records(id,kind,clip_id,path,sha256,manifest"
                ",created_at) VALUES(?,?,?,?,?,?,?)"
            ),
            (artifact_id, kind, clip_id, relative, digest, encode(manifest), now()),
        )
    return {
        "id": artifact_id,
        "kind": kind,
        "manifest": manifest,
        "download_url": f"/api/artifacts/{artifact_id}/file",
        "stale": False,
    }


def validate_png(content: bytes) -> tuple[int, int]:
    """Validate a bounded, non-animated 8-bit RGB/RGBA PNG without loading plugins."""

    def invalid():
        raise CatalogError("invalid_png", "Snapshot must be a bounded, valid RGB/RGBA PNG", 422)

    if not 45 <= len(content) <= MAX_PNG_BYTES or content[:8] != b"\x89PNG\r\n\x1a\n":
        invalid()
    position, width, height, channels = 8, 0, 0, 0
    compressed = bytearray()
    ended = False
    while position < len(content):
        if position + 12 > len(content):
            invalid()
        size = struct.unpack(">I", content[position : position + 4])[0]
        kind = content[position + 4 : position + 8]
        end = position + 12 + size
        if end > len(content):
            invalid()
        data = content[position + 8 : position + 8 + size]
        crc = struct.unpack(">I", content[position + 8 + size : end])[0]
        if zlib.crc32(kind + data) & 0xFFFFFFFF != crc:
            invalid()
        if position == 8 and kind != b"IHDR":
            invalid()
        if kind == b"IHDR":
            if width or size != 13:
                invalid()
            width, height, depth, color, compression, filtering, interlace = struct.unpack(
                ">IIBBBBB", data
            )
            if (
                not 1 <= width <= 4096
                or not 1 <= height <= 4096
                or width * height > 16_000_000
                or depth != 8
                or color not in {2, 6}
                or compression
                or filtering
                or interlace
            ):
                invalid()
            channels = 3 if color == 2 else 4
        elif kind == b"IDAT":
            compressed.extend(data)
        elif kind == b"IEND":
            if size or end != len(content):
                invalid()
            ended = True
        elif kind in {b"acTL", b"fcTL", b"fdAT"}:
            invalid()
        position = end
    if not ended or not compressed:
        invalid()
    expected = height * (width * channels + 1)
    decoder = zlib.decompressobj()
    try:
        raw = decoder.decompress(compressed, expected + 1)
        if (
            len(raw) != expected
            or not decoder.eof
            or decoder.unused_data
            or decoder.unconsumed_tail
        ):
            invalid()
        stride = width * channels + 1
        if any(raw[i] > 4 for i in range(0, len(raw), stride)):
            invalid()
    except zlib.error:
        invalid()
    return width, height


def stop_process(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                timeout=5,
                check=False,
                shell=False,
            )
        else:
            os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            if os.name != "nt":
                os.killpg(process.pid, signal.SIGKILL)
            process.kill()
            process.wait(timeout=3)
    except ProcessLookupError:
        pass


def run_bounded(
    command: list[str],
    *,
    cwd: Path,
    timeout: float = 120,
    cancel: threading.Event | None = None,
    output: Path | None = None,
    max_output: int = 16 * 1024 * 1024,
    env: dict | None = None,
    quota_dir: Path | None = None,
    quota_bytes: int = 2 * 1024**3,
) -> bytes:
    """Bound elapsed time, logs, and optional run-directory growth. Never shell out."""
    out = output or cwd / f"{uuid4()}.stdout.partial"
    err = cwd / f"{uuid4()}.stderr.partial"
    process = None
    try:
        with out.open("xb") as stdout, err.open("xb") as stderr:
            process = subprocess.Popen(
                command,
                cwd=cwd,
                stdout=stdout,
                stderr=stderr,
                stdin=subprocess.DEVNULL,
                shell=False,
                start_new_session=os.name != "nt",
                env=env,
            )
            deadline = time.monotonic() + timeout
            next_quota = 0.0
            while process.poll() is None:
                if cancel is not None and cancel.is_set():
                    raise CatalogError("job_cancelled", "The owner cancelled this job", 409)
                if time.monotonic() >= deadline:
                    raise CatalogError("process_timeout", "Processing exceeded its time limit", 422)
                if out.stat().st_size > max_output or err.stat().st_size > 2 * 1024 * 1024:
                    raise CatalogError(
                        "process_output_limit", "Processing exceeded its bounded output limit", 422
                    )
                if quota_dir is not None and time.monotonic() >= next_quota:
                    size = sum(
                        p.stat().st_size
                        for p in quota_dir.rglob("*")
                        if p.is_file() and not p.is_symlink()
                    )
                    if size > quota_bytes:
                        raise CatalogError(
                            "process_disk_limit", "Processing exceeded the per-run disk limit", 422
                        )
                    next_quota = time.monotonic() + 0.5
                time.sleep(0.025)
        if out.stat().st_size > max_output or err.stat().st_size > 2 * 1024 * 1024:
            raise CatalogError(
                "process_output_limit", "Processing exceeded its bounded output limit", 422
            )
        if quota_dir is not None:
            size = sum(
                p.stat().st_size for p in quota_dir.rglob("*") if p.is_file() and not p.is_symlink()
            )
            if size > quota_bytes:
                raise CatalogError(
                    "process_disk_limit", "Processing exceeded the per-run disk limit", 422
                )
        if process.returncode:
            detail = err.read_bytes()[-2000:].decode("utf-8", errors="replace")
            raise CatalogError(
                "process_failed", f"Local process exited with {process.returncode}: {detail}", 422
            )
        if cancel is not None and cancel.is_set():
            raise CatalogError("job_cancelled", "The owner cancelled this job", 409)
        return out.read_bytes()
    except FileNotFoundError as exc:
        raise CatalogError(
            "tool_missing", "Required local tool is missing. Run exais-football doctor", 503
        ) from exc
    finally:
        if process is not None:
            stop_process(process)
        if output is None:
            out.unlink(missing_ok=True)
        err.unlink(missing_ok=True)


def read_json(path: Path, limit: int = 16 * 1024 * 1024):
    if path.stat().st_size > limit:
        raise CatalogError("artifact_too_large", "An analysis artifact exceeds its size limit", 422)
    try:
        return json.loads(
            path.read_text(encoding="utf-8"),
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError("non-finite")),
        )
    except (ValueError, UnicodeError) as exc:
        raise CatalogError("invalid_json", "An artifact is not valid finite JSON", 422) from exc
