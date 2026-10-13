"""Source-bound football-cv suggestion import and an explicit external runner.

No model is imported, downloaded, or executed by this module. The recorded
upstream revision is provenance supplied by the operator, not an attestation
that a particular model executed. Per-play tracker IDs are never roster IDs.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path

from exais_football.media import MediaError

UPSTREAM_REPOSITORY = "https://github.com/andrewneuwirth/football-cv"
REVIEWED_UPSTREAM_REVISION = "38f6f758d3e78af3b700f144214c2d4566459c5d"
PROVIDER = "football-cv"
SCHEMA_VERSION = 1
POSITION_LABELS = frozenset({"DL", "LB", "CB", "S", "OL", "QB", "RB", "WR"})
MAX_POSITIONS = 10_000
_SHA256 = re.compile(r"[0-9a-fA-F]{64}\Z")
_REVISION = re.compile(r"[0-9a-fA-F]{40}\Z")
_TRACK_ID = re.compile(r"(?:0|[1-9][0-9]{0,11})\Z")
_PLAY_ID = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_-]{0,99}\Z")


def validate_positions(payload: dict, source_sha256: str) -> dict:
    if not isinstance(payload, dict):
        raise MediaError("invalid_analysis", "An analysis bundle must be a JSON object.")
    permitted = {
        "provider",
        "schema_version",
        "source_sha256",
        "upstream_revision",
        "positions",
        "notes",
    }
    if any(not isinstance(key, str) for key in payload) or set(payload) - permitted:
        raise MediaError("unknown_analysis_schema", "The analysis bundle has unsupported fields.")
    if payload.get("provider", PROVIDER) != PROVIDER:
        raise MediaError(
            "unknown_analysis_provider", "Only football-cv position bundles are supported."
        )
    version = payload.get("schema_version", SCHEMA_VERSION)
    if isinstance(version, bool) or not isinstance(version, int) or version != SCHEMA_VERSION:
        raise MediaError("unknown_analysis_schema", "The analysis schema version is unsupported.")
    provided_hash = payload.get("source_sha256")
    if (
        not isinstance(source_sha256, str)
        or not _SHA256.fullmatch(source_sha256)
        or not isinstance(provided_hash, str)
        or not _SHA256.fullmatch(provided_hash)
    ):
        raise MediaError(
            "invalid_source_hash", "Source SHA-256 values must contain 64 hexadecimal characters."
        )
    if provided_hash.lower() != source_sha256.lower():
        raise MediaError(
            "analysis_source_mismatch", "This analysis belongs to different source bytes."
        )
    revision = payload.get("upstream_revision")
    if not isinstance(revision, str) or not _REVISION.fullmatch(revision):
        raise MediaError(
            "invalid_upstream_revision", "Record the full 40-character upstream Git commit SHA."
        )
    positions = payload.get("positions")
    if not isinstance(positions, dict) or len(positions) > MAX_POSITIONS:
        raise MediaError(
            "invalid_positions", "Positions must be a bounded mapping of tracking IDs to labels."
        )
    for track_id, label in positions.items():
        if not isinstance(track_id, str) or not _TRACK_ID.fullmatch(track_id):
            raise MediaError(
                "invalid_track_id", "Tracking IDs must be canonical nonnegative integer strings."
            )
        if not isinstance(label, str) or label not in POSITION_LABELS:
            raise MediaError(
                "invalid_position_label",
                "A position label is outside the supported football groups.",
            )
    notes = payload.get("notes", "")
    if not isinstance(notes, str) or len(notes) > 8000 or "\x00" in notes:
        raise MediaError(
            "invalid_analysis_notes", "Analysis notes must be text of at most 8000 characters."
        )
    # allow_nan=False is also an explicit guard if future accepted fields become
    # numeric. Current positions and provenance fields intentionally allow no floats.
    try:
        canonical_input = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (ValueError, TypeError, UnicodeError) as exc:
        raise MediaError(
            "invalid_analysis", "The bundle contains non-finite or invalid JSON data."
        ) from exc
    return {
        "provider": PROVIDER,
        "schema_version": SCHEMA_VERSION,
        "source_sha256": provided_hash.lower(),
        "upstream_revision": revision.lower(),
        "positions": dict(positions),
        "notes": notes,
        "status": "unreviewed",
        "track_id_scope": "source_clip",
        "identity_resolution": "not_provided",
        "result_state": "suggestions_imported" if positions else "no_suggestions",
        "accuracy_status": "not_evaluated",
        "upstream_execution": "not_verified",
        "input_canonical_sha256": hashlib.sha256(canonical_input).hexdigest(),
    }


def _local_file(path: Path, purpose: str, *, resolve_symlinks: bool = True) -> Path:
    try:
        value = Path(path).expanduser()
        if "://" in str(value) or str(value).startswith(("\\\\", "//")):
            raise ValueError("not local")
        # Resolving a venv/bin/python symlink to the system binary loses the
        # virtual environment and its explicitly installed external toolkit.
        # Check its target exists, but retain the operator's interpreter path.
        if resolve_symlinks:
            value = value.resolve(strict=True)
        else:
            value = value.absolute()
        if not value.is_file():
            raise ValueError("not a file")
        return value
    except (OSError, TypeError, ValueError) as exc:
        raise MediaError(
            "invalid_runner_path", f"{purpose} must be an existing local file."
        ) from exc


def _validate_calibration(path: Path) -> None:
    try:
        if path.stat().st_size > 128 * 1024:
            raise ValueError("too large")
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("not an object")
        frame = data.get("frame", 0)
        if isinstance(frame, bool) or not isinstance(frame, int) or frame < 0:
            raise ValueError("invalid frame")
        points = data.get("points")
        if not isinstance(points, list) or not 4 <= len(points) <= 64:
            raise ValueError("four to 64 points required")
        for point in points:
            if not isinstance(point, dict):
                raise ValueError("invalid point")
            for field in ("px", "py", "fx", "fy"):
                value = point.get(field)
                if (
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(value)
                ):
                    raise ValueError("invalid coordinate")
            if (
                not 0 <= point["px"] <= 32768
                or not 0 <= point["py"] <= 32768
                or not 0 <= point["fx"] <= 120
                or not 0 <= point["fy"] <= 53.34
            ):
                raise ValueError("coordinate outside field")
        for x_key, y_key in (("px", "py"), ("fx", "fy")):
            coordinates = {(point[x_key], point[y_key]) for point in points}
            if len(coordinates) < 4:
                raise ValueError("duplicate coordinates")
            first, second, *rest = sorted(coordinates)
            if not any(
                abs(
                    (second[0] - first[0]) * (y - first[1])
                    - (second[1] - first[1]) * (x - first[0])
                )
                > 1e-8
                for x, y in rest
            ):
                raise ValueError("collinear calibration")
    except (OSError, ValueError, TypeError, OverflowError) as exc:
        raise MediaError(
            "invalid_calibration", "Supply a finite, nondegenerate football-cv field calibration."
        ) from exc


_RUNNER = (
    "import sys; from footballcv import analyze; "
    "analyze(video=sys.argv[1], calibration=sys.argv[2], "
    "data_dir=sys.argv[3], play_id=sys.argv[4])"
)


def build_run_command(
    python_executable: Path,
    video: Path,
    calibration: Path,
    data_dir: Path,
    play_id: str,
) -> list[str]:
    """Prepare, never execute, an explicitly configured external toolkit call.

    The operator supplies an isolated interpreter with the chosen upstream revision
    already installed. A future runner must separately authorize any upstream model
    acquisition. Persistent data_dir/play_id are mandatory, and values are passed as
    arguments to constant code rather than interpolated into Python or a shell.
    """
    interpreter = _local_file(python_executable, "Python executable", resolve_symlinks=False)
    clip = _local_file(video, "Video")
    calibration_path = _local_file(calibration, "Calibration")
    _validate_calibration(calibration_path)
    if not isinstance(play_id, str) or not _PLAY_ID.fullmatch(play_id):
        raise MediaError(
            "invalid_play_id",
            "Use a stable play ID containing letters, digits, underscores or hyphens.",
        )
    try:
        destination = Path(data_dir).expanduser()
        if not destination.is_absolute() or str(destination).startswith(("\\\\", "//")):
            raise ValueError("not a local absolute directory")
        destination = destination.resolve()
        if destination.exists() and not destination.is_dir():
            raise ValueError("not a directory")
        if (destination / "plays" / play_id / "clip.mp4").resolve() == clip:
            raise ValueError("upstream ingestion would copy the source onto itself")
    except (OSError, TypeError, ValueError) as exc:
        raise MediaError(
            "invalid_runner_directory", "Provide a separate persistent local analysis directory."
        ) from exc
    return [
        str(interpreter),
        "-I",
        "-c",
        _RUNNER,
        str(clip),
        str(calibration_path),
        str(destination),
        play_id,
    ]
