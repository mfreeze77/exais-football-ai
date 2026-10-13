"""Local, bounded media operations. Originals are inputs, never export targets.

FFmpeg/ffprobe are external executables resolved from the operator's PATH. A
successful export means its streams, duration and full decode were checked;
finding an executable alone does not mean a media workflow was qualified.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import stat
import subprocess
import tempfile
from fractions import Fraction
from pathlib import Path
from typing import Any

PROBE_TIMEOUT = 30.0
THUMBNAIL_TIMEOUT = 60.0
EXPORT_TIMEOUT = 600.0
VERIFY_TIMEOUT = 600.0
MAX_DURATION_SECONDS = 6 * 60 * 60
MAX_DIMENSION = 8192
MAX_PIXELS = 36_000_000
MAX_STDOUT_BYTES = 1024 * 1024
MAX_AUDIO_STREAMS = 16

# Deliberately exclude playlists, concat, DASH, HLS, devices and network
# protocols. Footage is one self-contained local video container.
VIDEO_FORMATS = "mov,matroska,webm,avi,mpegts,mpeg,ogg,flv,asf,mxf,nut"
IMAGE_FORMATS = "image2,jpeg_pipe,png_pipe"


class MediaError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _tool(name: str) -> str:
    executable = shutil.which(name)
    if not executable:
        raise MediaError(f"{name}_missing", f"{name} is not available on the local PATH.")
    return str(Path(executable).resolve())


def _run(command: list[str], *, timeout: float, purpose: str) -> bytes:
    """Check exit status; bound elapsed time and captured output memory.

    ffprobe does not implement FFmpeg's -nostdin option. DEVNULL also prevents it
    from reading interactive input. Temporary output files avoid unbounded PIPE
    buffers, and all readers consume at most the documented limit.
    """
    try:
        with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
            try:
                result = subprocess.run(
                    command,
                    stdin=subprocess.DEVNULL,
                    stdout=output,
                    stderr=errors,
                    shell=False,
                    check=False,
                    timeout=timeout,
                    close_fds=True,
                )
            except subprocess.TimeoutExpired as exc:
                raise MediaError("media_timeout", f"{purpose} exceeded its time limit.") from exc
            errors.seek(0, os.SEEK_END)
            errors.seek(max(0, errors.tell() - 2000))
            detail = errors.read(2000).decode("utf-8", errors="replace").strip()
            if result.returncode:
                raise MediaError(
                    "media_process_failed",
                    f"{purpose} failed: {detail or f'exit status {result.returncode}'}",
                )
            output.seek(0)
            data = output.read(MAX_STDOUT_BYTES + 1)
            if len(data) > MAX_STDOUT_BYTES:
                raise MediaError("media_output_limit", f"{purpose} returned too much metadata.")
            return data
    except OSError as exc:
        raise MediaError(
            "media_io_error", f"{purpose} could not access local files: {exc}"
        ) from exc


def _source(path: Path) -> Path:
    try:
        supplied = str(path)
        if "://" in supplied or supplied.startswith(("\\\\", "//")):
            raise MediaError("local_file_required", "Only a local file can be processed.")
        resolved = Path(path).expanduser().resolve(strict=True)
        if not stat.S_ISREG(resolved.stat().st_mode):
            raise MediaError("invalid_media", "The media input must be a regular file.")
        if resolved.stat().st_size == 0:
            raise MediaError("invalid_media", "The media input is empty.")
        return resolved
    except (OSError, TypeError, ValueError) as exc:
        raise MediaError("media_not_found", "The local media file could not be opened.") from exc


def _target(source: Path, destination: Path, *, suffixes: set[str]) -> Path:
    try:
        target = Path(destination).expanduser().absolute()
        if target.suffix.lower() not in suffixes:
            raise MediaError(
                "invalid_export_format", "The destination has an unsupported extension."
            )
        if target.resolve() == source or (target.exists() and target.samefile(source)):
            raise MediaError(
                "source_overwrite_forbidden", "A derived file cannot replace its source."
            )
        if target.exists() and not target.is_file():
            raise MediaError("invalid_destination", "The destination is not a regular file.")
        target.parent.mkdir(parents=True, exist_ok=True)
        return target
    except (OSError, TypeError, ValueError) as exc:
        raise MediaError("media_io_error", "The output directory is not writable.") from exc


def _local_input_options(*, image: bool = False) -> list[str]:
    return [
        "-protocol_whitelist",
        "file,pipe",
        "-format_whitelist",
        IMAGE_FORMATS if image else VIDEO_FORMATS,
        "-probesize",
        "10000000",
        "-analyzeduration",
        "10000000",
    ]


def _number(value: Any) -> float | None:
    try:
        if isinstance(value, bool):
            return None
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _rate(value: Any) -> float | None:
    try:
        number = float(Fraction(str(value)))
        return number if math.isfinite(number) and number > 0 else None
    except (ValueError, ZeroDivisionError, OverflowError):
        return None


def _probe_document(path: Path) -> dict:
    command = [
        _tool("ffprobe"),
        "-v",
        "error",
        "-max_alloc",
        "268435456",
        *_local_input_options(),
        "-show_entries",
        "stream=index,codec_type,codec_name,width,height,avg_frame_rate,r_frame_rate,"
        "duration,start_time:stream_disposition=attached_pic:stream_tags=rotate:"
        "stream_side_data=rotation:format=duration,format_name,start_time",
        "-print_format",
        "json",
        "-i",
        str(path),
    ]
    try:
        raw = _run(command, timeout=PROBE_TIMEOUT, purpose="Video probing")
        document = json.loads(raw)
        if not isinstance(document, dict):
            raise ValueError("not an object")
        return document
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
        raise MediaError("invalid_media", "ffprobe did not return valid video metadata.") from exc
    except MediaError as exc:
        if exc.code == "media_process_failed":
            raise MediaError("invalid_media", exc.message) from exc
        raise


def _parse_probe(document: dict) -> dict:
    streams = document.get("streams")
    if not isinstance(streams, list) or len(streams) > 64:
        raise MediaError("invalid_media", "The file has no usable stream metadata.")
    video = next(
        (
            stream
            for stream in streams
            if isinstance(stream, dict)
            and stream.get("codec_type") == "video"
            and not stream.get("disposition", {}).get("attached_pic")
        ),
        None,
    )
    if video is None:
        raise MediaError("video_stream_required", "The file has no video stream.")
    duration = _number(video.get("duration"))
    if duration is None or duration <= 0:
        duration = _number(document.get("format", {}).get("duration"))
    if duration is None or not 0 < duration <= MAX_DURATION_SECONDS:
        raise MediaError(
            "invalid_video_duration", "Video duration is unknown or outside local limits."
        )
    width, height = _number(video.get("width")), _number(video.get("height"))
    if (
        width is None
        or height is None
        or width != int(width)
        or height != int(height)
        or not 1 <= width <= MAX_DIMENSION
        or not 1 <= height <= MAX_DIMENSION
        or width * height > MAX_PIXELS
    ):
        raise MediaError("invalid_video_dimensions", "Video dimensions are invalid or too large.")
    rotation = _number(video.get("tags", {}).get("rotate")) or 0.0
    for side_data in video.get("side_data_list", []):
        if isinstance(side_data, dict) and "rotation" in side_data:
            rotation = _number(side_data["rotation"]) or 0.0
    if abs(rotation) % 180 == 90:
        width, height = height, width
    fps = _rate(video.get("avg_frame_rate")) or _rate(video.get("r_frame_rate"))
    if fps is None or fps > 1000:
        raise MediaError(
            "invalid_video_timing", "A finite video frame rate could not be established."
        )
    audio = [s for s in streams if isinstance(s, dict) and s.get("codec_type") == "audio"]
    if len(audio) > MAX_AUDIO_STREAMS:
        raise MediaError("media_stream_limit", "The video has too many audio streams.")
    return {
        "duration": duration,
        "width": int(width),
        "height": int(height),
        "fps": fps,
        "has_audio": bool(audio),
        "video_index": video.get("index", 0),
        "video_codec": video.get("codec_name"),
        "audio_streams": audio,
    }


def _public_probe(info: dict) -> dict:
    return {key: info[key] for key in ("duration", "width", "height", "fps", "has_audio")}


def probe_video(path: Path) -> dict:
    """Probe one supported local video; do not invent unknown duration or FPS."""
    return _public_probe(_parse_probe(_probe_document(_source(path))))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _temporary_target(destination: Path) -> Path:
    descriptor, name = tempfile.mkstemp(
        prefix=f".{destination.stem}-",
        suffix=destination.suffix,
        dir=destination.parent,
    )
    os.close(descriptor)
    return Path(name)


def _commit_output(staged: Path, destination: Path) -> None:
    with staged.open("rb") as handle:
        os.fsync(handle.fileno())
    os.replace(staged, destination)


def _ffmpeg_prefix() -> list[str]:
    return [
        _tool("ffmpeg"),
        "-hide_banner",
        "-nostdin",
        "-v",
        "error",
        "-xerror",
        "-y",
        "-max_alloc",
        "268435456",
        "-filter_threads",
        "1",
        "-filter_complex_threads",
        "1",
    ]


def _decode_check(path: Path, *, image: bool = False) -> None:
    _run(
        [
            *_ffmpeg_prefix(),
            *_local_input_options(image=image),
            "-threads",
            "2",
            "-i",
            str(path),
            "-map",
            "0:v:0",
            "-map",
            "0:a?",
            "-sn",
            "-dn",
            "-threads",
            "2",
            "-f",
            "null",
            "-",
        ],
        timeout=THUMBNAIL_TIMEOUT if image else VERIFY_TIMEOUT,
        purpose="Output decode verification",
    )


def generate_thumbnail(source: Path, destination: Path, at_seconds: float = 0) -> Path:
    source = _source(source)
    info = _parse_probe(_probe_document(source))
    time = _number(at_seconds)
    if time is None or not 0 <= time < info["duration"]:
        raise MediaError("invalid_thumbnail_time", "Thumbnail time must fall within the video.")
    destination = _target(source, destination, suffixes={".jpg", ".jpeg", ".png"})
    staged: Path | None = None
    try:
        staged = _temporary_target(destination)
        _run(
            [
                *_ffmpeg_prefix(),
                *_local_input_options(),
                "-threads",
                "2",
                "-i",
                str(source),
                "-ss",
                f"{time:.9f}",
                "-map",
                f"0:{info['video_index']}",
                "-frames:v",
                "1",
                "-an",
                "-sn",
                "-dn",
                "-vf",
                "scale=640:360:force_original_aspect_ratio=decrease:force_divisible_by=2",
                "-threads",
                "1",
                "-q:v",
                "3",
                "-f",
                "image2",
                "-update",
                "1",
                str(staged),
            ],
            timeout=THUMBNAIL_TIMEOUT,
            purpose="Thumbnail generation",
        )
        if staged.stat().st_size == 0:
            raise MediaError("thumbnail_verification_failed", "No thumbnail frame was produced.")
        _decode_check(staged, image=True)
        _commit_output(staged, destination)
        return destination
    except OSError as exc:
        raise MediaError("media_io_error", "The thumbnail could not be saved.") from exc
    finally:
        if staged is not None:
            staged.unlink(missing_ok=True)


def _verify_export(path: Path, source_info: dict, requested_duration: float) -> dict:
    info = _parse_probe(_probe_document(path))
    tolerance = max(0.1, 2.0 / source_info["fps"])
    if abs(info["duration"] - requested_duration) > tolerance:
        raise MediaError(
            "export_duration_mismatch", "Export duration did not match the selected interval."
        )
    if info["video_codec"] != "h264":
        raise MediaError("export_codec_mismatch", "The export is not H.264 video.")
    expected_width = source_info["width"] + source_info["width"] % 2
    expected_height = source_info["height"] + source_info["height"] % 2
    if (info["width"], info["height"]) != (expected_width, expected_height):
        raise MediaError("export_dimension_mismatch", "Export dimensions do not match the source.")
    if len(info["audio_streams"]) != len(source_info["audio_streams"]):
        raise MediaError(
            "export_audio_missing", "The export did not preserve every source audio stream."
        )
    if any(s.get("codec_name") != "aac" for s in info["audio_streams"]):
        raise MediaError("export_audio_codec_mismatch", "The export audio is not AAC.")
    _decode_check(path)
    return {
        **_public_probe(info),
        "verification": {
            "full_decode": "passed",
            "video_codec": "h264",
            "audio_stream_count": len(info["audio_streams"]),
            "audio_preservation": "verified" if source_info["has_audio"] else "not_applicable",
            "duration_tolerance_seconds": tolerance,
        },
    }


def export_trim(
    source: Path,
    destination: Path,
    start_seconds: float,
    end_seconds: float,
) -> dict:
    """Accurate, audio-preserving H.264/AAC MP4 export with atomic publication.

    Output-side seeking decodes through the in-point instead of using keyframe
    stream-copy cuts. Frame quantization and AAC priming permit the returned small
    duration tolerance. All original audio streams are explicitly mapped. The
    existing destination remains untouched if encoding or verification fails.
    """
    source = _source(source)
    info = _parse_probe(_probe_document(source))
    start, end = _number(start_seconds), _number(end_seconds)
    if start is None or end is None or not 0 <= start < end <= info["duration"]:
        raise MediaError("invalid_trim", "Trim must satisfy 0 <= start < end <= source duration.")
    destination = _target(source, destination, suffixes={".mp4"})
    staged: Path | None = None
    try:
        source_hash = _sha256(source)
        staged = _temporary_target(destination)
        _run(
            [
                *_ffmpeg_prefix(),
                *_local_input_options(),
                "-threads",
                "2",
                "-i",
                str(source),
                "-ss",
                f"{start:.9f}",
                "-t",
                f"{end - start:.9f}",
                "-map",
                f"0:{info['video_index']}",
                "-map",
                "0:a?",
                "-sn",
                "-dn",
                "-map_metadata",
                "-1",
                "-vf",
                "pad=ceil(iw/2)*2:ceil(ih/2)*2",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "18",
                "-pix_fmt",
                "yuv420p",
                "-threads",
                "2",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-movflags",
                "+faststart",
                "-f",
                "mp4",
                str(staged),
            ],
            timeout=EXPORT_TIMEOUT,
            purpose="Video export",
        )
        result = _verify_export(staged, info, end - start)
        if _sha256(source) != source_hash:
            raise MediaError(
                "source_changed", "The source changed during export; the output was withheld."
            )
        result.update(
            {
                "source_sha256": source_hash,
                "output_sha256": _sha256(staged),
                "start_seconds": start,
                "end_seconds": end,
                "time_origin": "source_file",
            }
        )
        _commit_output(staged, destination)
        return result
    except OSError as exc:
        raise MediaError("media_io_error", f"The export could not be saved: {exc}") from exc
    finally:
        if staged is not None:
            staged.unlink(missing_ok=True)


def media_capabilities() -> dict:
    result = {}
    for name in ("ffmpeg", "ffprobe"):
        try:
            executable = _tool(name)
            text = _run([executable, "-version"], timeout=5, purpose=f"{name} capability check")
            first_line = text.decode("utf-8", errors="replace").splitlines()[0]
            result[name] = {
                "available": True,
                "status": "executable_available",
                "version": first_line,
                "verification": "executable_only",
            }
        except (MediaError, IndexError) as exc:
            result[name] = {
                "available": False,
                "status": "unavailable",
                "reason": exc.message if isinstance(exc, MediaError) else "No version response.",
            }
    return result
