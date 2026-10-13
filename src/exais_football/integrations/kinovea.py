"""Kinovea file handoff, safe KVA envelope validation and opt-in Windows launch.

The XML names and version were checked against Kinovea's MetadataSerializer at
13d0236f65622501da5cb776ddb979629f468e71. No Kinovea implementation is embedded.
Validation preserves bytes; it does not prove that annotations describe a clip.
"""

from __future__ import annotations

import hashlib
import platform
import subprocess
from pathlib import Path
from xml.parsers import expat

from exais_football.media import MediaError

MAX_KVA_BYTES = 2 * 1024 * 1024
MAX_XML_ELEMENTS = 100_000
MAX_XML_DEPTH = 64
SUPPORTED_FORMAT_VERSIONS = frozenset({"2.0", "2.1"})
UPSTREAM_REVISION = "13d0236f65622501da5cb776ddb979629f468e71"


def capability(executable: str | None) -> dict:
    if platform.system() != "Windows":
        return {
            "available": False,
            "status": "unsupported_platform",
            "reason": "Kinovea desktop launch requires Windows; KVA sidecars can still be stored.",
        }
    if not executable:
        return {
            "available": False,
            "status": "not_configured",
            "reason": "Set the Kinovea executable in local operator configuration.",
        }
    try:
        path = Path(executable).expanduser()
        if (
            not path.is_absolute()
            or str(path).startswith(("\\\\", "//"))
            or path.suffix.lower() != ".exe"
            or not path.is_file()
        ):
            raise ValueError("not a local executable")
    except (OSError, TypeError, ValueError):
        return {
            "available": False,
            "status": "invalid_configuration",
            "reason": "The configured Kinovea path must point to an existing local .exe file.",
        }
    return {
        "available": True,
        "status": "configured",
        "reason": "The Windows executable is configured; native operation is not yet verified.",
        "native_validation": "not_verified",
    }


def validate_kva(content: bytes) -> dict:
    """Validate the safe, known XML envelope without altering original bytes.

    Expat declaration callbacks reject DTDs/entities independently of input
    encoding (including UTF-16). No entity resolver or external resource is used.
    Only versions whose envelope was inspected are accepted. Drawing geometry is
    preserved, not interpreted or certified as native-compatible.
    """
    if not isinstance(content, bytes) or not content:
        raise MediaError("invalid_kva", "A nonempty KVA byte stream is required.")
    if len(content) > MAX_KVA_BYTES:
        raise MediaError("kva_too_large", "KVA sidecars are limited to 2 MiB.")
    parser = expat.ParserCreate(namespace_separator="}")
    parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
    depth = count = 0
    root = None
    version_parts: list[str] = []
    version_depth: int | None = None
    version_count = 0

    def forbidden(*args):
        raise MediaError(
            "unsafe_kva_xml", "KVA files cannot contain DTDs, entities or processing instructions."
        )

    def start(name, attributes):
        nonlocal depth, count, root, version_depth, version_count
        depth += 1
        count += 1
        if depth > MAX_XML_DEPTH or count > MAX_XML_ELEMENTS or len(attributes) > 128:
            raise MediaError(
                "kva_complexity_limit", "The KVA document exceeds safe structural limits."
            )
        if depth == 1:
            root = name
            if name != "KinoveaVideoAnalysis":
                raise MediaError("invalid_kva_root", "The XML root must be KinoveaVideoAnalysis.")
        if name == "FormatVersion":
            if depth != 2:
                raise MediaError("invalid_kva_schema", "FormatVersion must be a direct root child.")
            version_count += 1
            version_depth = depth
        elif version_depth is not None:
            raise MediaError("invalid_kva_schema", "FormatVersion cannot contain nested elements.")

    def end(name):
        nonlocal depth, version_depth
        if name == "FormatVersion" and depth == version_depth:
            version_depth = None
        depth -= 1

    def characters(value):
        if version_depth is not None:
            version_parts.append(value)
            if sum(map(len, version_parts)) > 32:
                raise MediaError("invalid_kva_schema", "FormatVersion is too long.")

    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.CharacterDataHandler = characters
    parser.StartDoctypeDeclHandler = forbidden
    parser.EntityDeclHandler = forbidden
    parser.ExternalEntityRefHandler = forbidden
    parser.ProcessingInstructionHandler = forbidden
    try:
        parser.Parse(content, True)
    except expat.ExpatError as exc:
        raise MediaError("invalid_kva_xml", "The KVA sidecar is not well-formed XML.") from exc
    version = "".join(version_parts).strip()
    if root is None or version_count != 1:
        raise MediaError("invalid_kva_schema", "Exactly one FormatVersion element is required.")
    if version not in SUPPORTED_FORMAT_VERSIONS:
        raise MediaError(
            "unsupported_kva_version", f"KVA format {version!r} has not been qualified for import."
        )
    return {
        "root": root,
        "format_version": version,
        "byte_length": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
        "element_count": count,
        "validation": "safe_xml_envelope",
        "native_compatibility": "not_verified",
        "source_binding": "not_verified",
        "bytes_preserved": True,
    }


def launch(executable: str, video: Path) -> None:
    """Open an allowlisted video using operator-configured Kinovea on Windows.

    The GUI is intentionally long-lived. Only startup is checked for a bounded
    interval; returning does not claim the application rendered the clip.
    """
    state = capability(executable)
    if not state["available"]:
        raise MediaError("kinovea_unavailable", state["reason"])
    try:
        target = Path(video).expanduser()
        if str(target).startswith(("\\\\", "//")) or not target.is_file():
            raise MediaError("media_not_found", "The local video handoff file does not exist.")
        target = target.resolve(strict=True)
        program = Path(executable).expanduser().resolve(strict=True)
        process = subprocess.Popen(
            [str(program), str(target)],
            shell=False,
            cwd=str(program.parent),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
        try:
            returncode = process.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            return
        if returncode != 0:
            raise MediaError("kinovea_launch_failed", f"Kinovea exited with status {returncode}.")
    except OSError as exc:
        raise MediaError(
            "kinovea_launch_failed", "The configured Kinovea executable could not be started."
        ) from exc
