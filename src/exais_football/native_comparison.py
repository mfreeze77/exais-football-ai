"""Opt-in, version-gated Kinovea handoff using independent verified copies.

The inspected command line accepts -video and -video2, not a synchronization
argument. Anchors are exported as instructions; native synchronization is manual.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from uuid import uuid4

from .integrations.kinovea import capability
from .service import CatalogError, encode, file_sha256
from .study import Study
from .workspace import atomic_bytes, register_artifact, workspace_lock


def executable_version(path: Path) -> tuple[int, int, int, int]:
    """Read Windows VERSIONINFO without executing the configured application."""
    if os.name != "nt":
        raise CatalogError("kinovea_platform", "Native handoff requires Windows", 503)
    import ctypes
    from ctypes import wintypes

    version = ctypes.WinDLL("version", use_last_error=True)
    version.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
    version.GetFileVersionInfoSizeW.restype = wintypes.DWORD
    version.GetFileVersionInfoW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
    ]
    version.GetFileVersionInfoW.restype = wintypes.BOOL
    version.VerQueryValueW.argtypes = [
        ctypes.c_void_p,
        wintypes.LPCWSTR,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.UINT),
    ]
    version.VerQueryValueW.restype = wintypes.BOOL
    size = version.GetFileVersionInfoSizeW(str(path), None)
    if not 0 < size <= 1024 * 1024:
        raise CatalogError("kinovea_version", "Could not read the Kinovea file version", 422)
    buffer = ctypes.create_string_buffer(size)
    address, length = ctypes.c_void_p(), wintypes.UINT()
    if not version.GetFileVersionInfoW(str(path), 0, size, buffer) or not version.VerQueryValueW(
        buffer, "\\", ctypes.byref(address), ctypes.byref(length)
    ):
        raise CatalogError("kinovea_version", "Could not read the Kinovea file version", 422)
    if length.value < 13 * ctypes.sizeof(wintypes.DWORD):
        raise CatalogError("kinovea_version", "Invalid Kinovea version resource", 422)
    info = ctypes.cast(address, ctypes.POINTER(wintypes.DWORD))
    if info[0] != 0xFEEF04BD:
        raise CatalogError("kinovea_version", "Invalid Kinovea version signature", 422)
    return info[2] >> 16, info[2] & 65535, info[3] >> 16, info[3] & 65535


def launch_comparison(catalog, comparison_id: str):
    from .app import _copy_review_file

    executable = catalog.settings.kinovea_executable
    state = capability(executable)
    if not state["available"]:
        raise CatalogError("kinovea_unavailable", state["reason"], 503)
    program = Path(executable).resolve(strict=True)
    version = executable_version(program)
    if version[:2] not in {(2025, 1), (2025, 2)}:
        raise CatalogError(
            "kinovea_version_unsupported",
            (
                "This native version has not passed the inspected "
                "2025.1/2025.2 command profile. Use browser "
                "comparison or retain a tested installation."
            ),
            422,
        )
    record = Study(catalog).comparison(comparison_id)
    handoff_id = str(uuid4())
    relative = f"reviews/comparisons/{handoff_id}"
    directory = catalog.settings.path(relative)
    directory.mkdir(parents=True)
    videos = []
    with workspace_lock(catalog):
        for side in ("left", "right"):
            clip = record[f"{side}_snapshot"]
            source = catalog.source_path(clip["id"])
            if file_sha256(source) != clip["source_sha256"]:
                raise CatalogError("source_changed", "A comparison source changed", 409)
            target = directory / f"{clip['id']}-{side}{source.suffix}"
            _copy_review_file(source, target, clip["source_sha256"])
            videos.append(target)
            with catalog.db.connection() as db:
                sidecar = db.execute(
                    "SELECT * FROM sidecars WHERE clip_id=?", (clip["id"],)
                ).fetchone()
            if sidecar:
                _copy_review_file(
                    catalog.settings.path(sidecar["path"]),
                    target.with_suffix(".kva"),
                    sidecar["sha256"],
                )
        args = [
            str(program),
            "-video",
            str(videos[0]),
            "-video2",
            str(videos[1]),
            "-speed",
            "100",
            "-stretch",
        ]
        manifest = {
            "schema_version": 1,
            "comparison_id": comparison_id,
            "comparison_version": record["version"],
            "comparison": record,
            "executable_sha256": file_sha256(program),
            "executable_version": list(version),
            "copies": [p.relative_to(catalog.settings.data_dir).as_posix() for p in videos],
            "synchronization": "manual_native_anchors_required",
            "instructions": (
                "Align both native players at the saved left/right "
                "source anchors. The native CLI has no offset argument."
            ),
            "native_validation": "launch_only_not_visual_qualification",
        }
        receipt_path = directory / "handoff.json"
        atomic_bytes(receipt_path, encode(manifest).encode("utf-8"))
        try:
            process = subprocess.Popen(
                args,
                shell=False,
                cwd=program.parent,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
            )
            try:
                code = process.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                code = None
            if code not in {None, 0}:
                raise CatalogError(
                    "kinovea_launch_failed", "Kinovea rejected the comparison handoff", 503
                )
        except OSError as exc:
            raise CatalogError(
                "kinovea_launch_failed", "Could not launch the configured Kinovea executable", 503
            ) from exc
        receipt = register_artifact(catalog, "native_handoff", None, receipt_path, manifest)
    return {"status": "launch_requested", "receipt": receipt, **manifest}
