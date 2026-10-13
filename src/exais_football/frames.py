"""Source-bound presentation-time indexes and exact decoded-frame previews."""

from __future__ import annotations

import json
import math
import shutil
from pathlib import Path
from uuid import uuid4

from .service import CatalogError, encode, file_sha256
from .workspace import atomic_bytes, read_json, run_bounded, workspace_lock

INDEX_VERSION = 3
MAX_FRAMES = 100_000
MAX_FRAME_CACHE = 128 * 1024 * 1024


def verified_source(catalog, clip_id: str):
    clip = catalog.get_clip(clip_id)
    source = catalog.source_path(clip_id)
    if file_sha256(source) != clip["source_sha256"]:
        raise CatalogError(
            "source_changed", "The original video no longer matches its preserved hash", 409
        )
    return clip, source


def frame_index(catalog, clip_id: str, *, create: bool = False) -> dict:
    clip, source = verified_source(catalog, clip_id)
    root = catalog.settings.path(f"cache/frames/{clip['source_sha256']}")
    root.mkdir(parents=True, exist_ok=True)
    manifest = root / "index.json"
    if manifest.is_file():
        try:
            index = read_json(manifest)
            if (
                index["schema_version"] == INDEX_VERSION
                and index["source_sha256"] == clip["source_sha256"]
                and index["width"] == clip["width"]
                and index["height"] == clip["height"]
                and index["count"] == len(index["timestamps"]) == len(index["raw_timestamps"])
                and 0 < index["count"] <= MAX_FRAMES
                and all(type(t) in (int, float) and math.isfinite(t) for t in index["timestamps"])
                and all(type(t) is int for t in index["raw_timestamps"])
                and all(
                    b > a
                    for a, b in zip(index["timestamps"], index["timestamps"][1:], strict=False)
                )
            ):
                return index
        except (CatalogError, KeyError, TypeError):
            pass
    if not create:
        return {
            "status": "needs_index",
            "source_sha256": clip["source_sha256"],
            "reason": "Build the optional frame index; ordinary browser seeking is approximate",
        }
    tool = shutil.which("ffprobe")
    if not tool:
        raise CatalogError("tool_missing", "Install FFmpeg and ffprobe to index frames", 503)
    output = run_bounded(
        [
            tool,
            "-v",
            "error",
            "-protocol_whitelist",
            "file,pipe",
            "-select_streams",
            "v:0",
            "-show_frames",
            "-show_format",
            "-show_streams",
            "-show_entries",
            "frame=best_effort_timestamp_time,best_effort_timestamp:format=start_time:stream=time_base:stream_side_data=rotation:stream_tags=rotate",
            "-of",
            "json",
            str(source),
        ],
        cwd=root,
        timeout=90,
    )
    data = json.loads(output)
    try:
        origin = float(data.get("format", {}).get("start_time", 0))
        raw_timestamps = [int(frame["best_effort_timestamp"]) for frame in data["frames"]]
        stream = data["streams"][0]
        time_base = stream["time_base"]
        rotation = float(stream.get("tags", {}).get("rotate", 0))
        for item in stream.get("side_data_list", []):
            if "rotation" in item:
                rotation = float(item["rotation"])
        if not math.isfinite(rotation):
            raise ValueError("Invalid display rotation")
        timestamps = [
            float(frame["best_effort_timestamp_time"]) - origin for frame in data["frames"]
        ]
        if (
            not math.isfinite(origin)
            or not 1 <= len(timestamps) <= MAX_FRAMES
            or any(not math.isfinite(t) or t < -0.001 for t in timestamps)
            or any(b <= a for a, b in zip(timestamps, timestamps[1:], strict=False))
        ):
            raise ValueError("Invalid frame timeline")
    except (KeyError, TypeError, ValueError) as exc:
        raise CatalogError(
            "frame_index_unsupported",
            "This source lacks a bounded strictly increasing presentation timeline",
            422,
        ) from exc
    index = {
        "schema_version": INDEX_VERSION,
        "status": "ready",
        "source_sha256": clip["source_sha256"],
        "source_asset_id": clip["source_asset_id"],
        "width": clip["width"],
        "height": clip["height"],
        "format_start_time": origin,
        "rotation_degrees": rotation,
        "timestamps": timestamps,
        "raw_timestamps": raw_timestamps,
        "time_base": time_base,
        "count": len(timestamps),
        "max_cached_image_bytes": MAX_FRAME_CACHE,
        "method": "ffprobe_best_effort_presentation_timestamps",
    }
    if file_sha256(source) != clip["source_sha256"]:
        raise CatalogError("source_changed", "The source changed during indexing", 409)
    with workspace_lock(catalog):
        manifest.unlink(missing_ok=True)
        atomic_bytes(manifest, encode(index).encode())
    return index


def frame_image(catalog, clip_id: str, index: int) -> Path:
    metadata = frame_index(catalog, clip_id, create=False)
    if metadata["status"] != "ready":
        raise CatalogError("frame_index_required", "Build the frame index first", 409)
    if not 0 <= index < metadata["count"]:
        raise CatalogError("frame_out_of_range", "Choose an indexed decoded frame", 422)
    root = catalog.settings.path(f"cache/frames/{metadata['source_sha256']}")
    output = root / f"{index:06}.png"
    if output.is_file():
        return output
    tool = shutil.which("ffmpeg")
    if not tool:
        raise CatalogError("tool_missing", "Install FFmpeg for exact decoded-frame previews", 503)
    source = catalog.source_path(clip_id)
    temporary = root / f"{uuid4()}.png"
    try:
        run_bounded(
            [
                tool,
                "-nostdin",
                "-v",
                "error",
                "-protocol_whitelist",
                "file,pipe",
                "-i",
                str(source),
                "-vf",
                f"select=eq(n\\,{index}),scale=w='min(1280,iw)':h=-2",
                "-fps_mode",
                "vfr",
                "-frames:v",
                "1",
                "-threads",
                "1",
                str(temporary),
            ],
            cwd=root,
            timeout=90,
        )
        if not temporary.is_file() or temporary.stat().st_size > 8 * 1024 * 1024:
            raise CatalogError(
                "frame_decode_failed",
                "The requested frame could not be decoded within preview limits",
                422,
            )
        verified_source(catalog, clip_id)
        with workspace_lock(catalog):
            from .storage import leased

            images = sorted(
                (p for p in root.glob("*.png") if p != temporary and not leased(p)),
                key=lambda p: p.stat().st_mtime,
            )
            total = sum(p.stat().st_size for p in images) + temporary.stat().st_size
            for image in images:
                if total <= MAX_FRAME_CACHE:
                    break
                total -= image.stat().st_size
                image.unlink(missing_ok=True)
            if not output.exists():
                temporary.replace(output)
        return output
    finally:
        temporary.unlink(missing_ok=True)


def timeline_preview(catalog, clip_id: str, count: int = 9) -> dict:
    """Return a bounded, source-bound strip; image bytes are decoded lazily."""
    if not 2 <= count <= 12:
        raise CatalogError("preview_count", "Choose 2 to 12 timeline previews", 422)
    index = frame_index(catalog, clip_id, create=False)
    if index["status"] != "ready":
        return {**index, "items": []}
    numbers = sorted({round(i * (index["count"] - 1) / (count - 1)) for i in range(count)})
    return {
        "status": "ready",
        "source_sha256": index["source_sha256"],
        "cache_limit_bytes_per_source": MAX_FRAME_CACHE,
        "items": [
            {
                "frame": n,
                "at_seconds": index["timestamps"][n],
                "image_url": f"/api/study/clips/{clip_id}/frames/{n}",
            }
            for n in numbers
        ],
    }
