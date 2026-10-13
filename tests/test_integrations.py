"""Integration contracts, hostile-input rejection and non-executing preparation.

KVA validation tests prove safe parsing only. No native Windows Kinovea launch
or football-cv model execution is claimed by this suite.
"""

from __future__ import annotations

import hashlib
import json
import math
import sys
from pathlib import Path

import pytest

from exais_football.integrations import football_cv, kinovea
from exais_football.media import MediaError


def kva(version="2.1") -> bytes:
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        f"<KinoveaVideoAnalysis><FormatVersion>{version}</FormatVersion>"
        "<Producer>Kinovea.test</Producer><GlobalTitle>A &amp; B</GlobalTitle>"
        "<Keyframes /></KinoveaVideoAnalysis>"
    ).encode()


def test_safe_kva_envelope_preserves_original_bytes():
    original = kva()
    snapshot = bytes(original)
    result = kinovea.validate_kva(original)
    assert original == snapshot
    assert result["sha256"] == hashlib.sha256(original).hexdigest()
    assert result["format_version"] == "2.1"
    assert result["bytes_preserved"] is True
    assert result["native_compatibility"] == "not_verified"
    assert result["source_binding"] == "not_verified"


def test_kva_utf16_is_parsed_safely():
    content = kva("2.0").decode().replace('encoding="utf-8"', 'encoding="utf-16"').encode("utf-16")
    assert kinovea.validate_kva(content)["format_version"] == "2.0"


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16"])
def test_xml_entities_and_dtd_are_rejected_in_every_encoding(encoding):
    content = (
        f'<?xml version="1.0" encoding="{encoding}"?>'
        '<!DOCTYPE KinoveaVideoAnalysis [<!ENTITY source SYSTEM "file:///etc/passwd">]>'
        "<KinoveaVideoAnalysis><FormatVersion>2.1</FormatVersion>"
        "<GlobalTitle>&source;</GlobalTitle></KinoveaVideoAnalysis>"
    ).encode(encoding)
    with pytest.raises(MediaError) as error:
        kinovea.validate_kva(content)
    assert error.value.code == "unsafe_kva_xml"


@pytest.mark.parametrize(
    "content,code",
    [
        (b"<WrongRoot><FormatVersion>2.1</FormatVersion></WrongRoot>", "invalid_kva_root"),
        (b"<KinoveaVideoAnalysis/>", "invalid_kva_schema"),
        (b"<KinoveaVideoAnalysis>", "invalid_kva_xml"),
        (kva("99.0"), "unsupported_kva_version"),
        (
            b"<?xml-stylesheet href='https://example.invalid/a'?>" + kva().split(b"\n", 1)[1],
            "unsafe_kva_xml",
        ),
        (
            b"<KinoveaVideoAnalysis><FormatVersion>2.1</FormatVersion><FormatVersion>2.1</FormatVersion></KinoveaVideoAnalysis>",
            "invalid_kva_schema",
        ),
    ],
)
def test_invalid_or_unknown_kva_is_rejected(content, code):
    with pytest.raises(MediaError) as error:
        kinovea.validate_kva(content)
    assert error.value.code == code


def test_kva_size_and_depth_limits():
    with pytest.raises(MediaError) as error:
        kinovea.validate_kva(b" " * (kinovea.MAX_KVA_BYTES + 1))
    assert error.value.code == "kva_too_large"
    nested = (
        b"<KinoveaVideoAnalysis><FormatVersion>2.1</FormatVersion>"
        + b"<a>" * 65
        + b"</a>" * 65
        + b"</KinoveaVideoAnalysis>"
    )
    with pytest.raises(MediaError) as error:
        kinovea.validate_kva(nested)
    assert error.value.code == "kva_complexity_limit"


def test_kinovea_launch_is_blocked_off_windows(monkeypatch, tmp_path):
    monkeypatch.setattr(kinovea.platform, "system", lambda: "Linux")
    assert kinovea.capability(None)["status"] == "unsupported_platform"
    with pytest.raises(MediaError) as error:
        kinovea.launch("/configured/Kinovea.exe", tmp_path / "source.mp4")
    assert error.value.code == "kinovea_unavailable"


def test_kinovea_configuration_does_not_claim_native_verification(monkeypatch, tmp_path):
    monkeypatch.setattr(kinovea.platform, "system", lambda: "Windows")
    assert kinovea.capability(None)["status"] == "not_configured"
    assert not kinovea.capability(str(tmp_path / "missing.exe"))["available"]
    executable = tmp_path / "Kinovea.exe"
    executable.write_bytes(b"placeholder; existence is not a native qualification")
    result = kinovea.capability(str(executable))
    assert result["available"] is True
    assert result["native_validation"] == "not_verified"


def test_kinovea_nonzero_launch_reports_failure_with_argument_array(monkeypatch, tmp_path):
    monkeypatch.setattr(kinovea.platform, "system", lambda: "Windows")
    executable = tmp_path / "Kinovea.exe"
    executable.write_bytes(b"test stub")
    video = tmp_path / "film & notes.mp4"
    video.write_bytes(b"test stub")
    calls = []

    class FailedProcess:
        def wait(self, timeout):
            assert timeout == 0.5
            return 7

    def failed_start(command, **options):
        calls.append((command, options))
        return FailedProcess()

    monkeypatch.setattr(kinovea.subprocess, "Popen", failed_start)
    with pytest.raises(MediaError) as error:
        kinovea.launch(str(executable), video)
    assert error.value.code == "kinovea_launch_failed"
    command, options = calls[0]
    assert command == [str(executable), str(video)]
    assert options["shell"] is False


def bundle() -> dict:
    return {
        "source_sha256": "a" * 64,
        "upstream_revision": football_cv.REVIEWED_UPSTREAM_REVISION,
        "positions": {"1": "QB", "23": "S"},
        "notes": "Imported suggestions, awaiting human review.",
    }


def test_ai_bundle_is_source_bound_unreviewed_and_not_roster_identity():
    payload = bundle()
    saved = json.dumps(payload, sort_keys=True)
    result = football_cv.validate_positions(payload, "a" * 64)
    assert json.dumps(payload, sort_keys=True) == saved
    assert result["positions"] == payload["positions"]
    assert result["positions"] is not payload["positions"]
    assert result["status"] == "unreviewed"
    assert result["track_id_scope"] == "source_clip"
    assert result["identity_resolution"] == "not_provided"
    assert result["accuracy_status"] == "not_evaluated"
    assert result["upstream_execution"] == "not_verified"
    assert len(result["input_canonical_sha256"]) == 64


@pytest.mark.parametrize(
    "changes,code",
    [
        ({"source_sha256": "b" * 64}, "analysis_source_mismatch"),
        ({"source_sha256": "not-a-hash"}, "invalid_source_hash"),
        ({"upstream_revision": "main"}, "invalid_upstream_revision"),
        ({"provider": "mystery"}, "unknown_analysis_provider"),
        ({"schema_version": 2}, "unknown_analysis_schema"),
        ({"schema_version": True}, "unknown_analysis_schema"),
        ({"roster_id": "123"}, "unknown_analysis_schema"),
        ({"positions": {"../1": "QB"}}, "invalid_track_id"),
        ({"positions": {"01": "QB"}}, "invalid_track_id"),
        ({"positions": {1: "QB"}}, "invalid_track_id"),
        ({"positions": {"1": "SLANT"}}, "invalid_position_label"),
        ({"positions": {"1": math.nan}}, "invalid_position_label"),
        ({"notes": math.inf}, "invalid_analysis_notes"),
    ],
)
def test_ai_unknown_mismatched_or_nonfinite_payloads_rejected(changes, code):
    payload = bundle() | changes
    with pytest.raises(MediaError) as error:
        football_cv.validate_positions(payload, "a" * 64)
    assert error.value.code == code


def test_empty_ai_positions_are_not_a_successful_detection_claim():
    result = football_cv.validate_positions(bundle() | {"positions": {}}, "a" * 64)
    assert result["result_state"] == "no_suggestions"
    assert result["accuracy_status"] == "not_evaluated"


@pytest.fixture
def runner_inputs(tmp_path):
    clip = tmp_path / "film; do-not-execute.mp4"
    clip.write_bytes(b"not executed by the command builder")
    calibration = tmp_path / "calibration.json"
    calibration.write_text(
        json.dumps(
            {
                "frame": 10,
                "points": [
                    {"px": 0, "py": 0, "fx": 30, "fy": 0},
                    {"px": 160, "py": 0, "fx": 45, "fy": 0},
                    {"px": 0, "py": 90, "fx": 30, "fy": 53.33},
                    {"px": 160, "py": 90, "fx": 45, "fy": 53.33},
                ],
            }
        )
    )
    return clip, calibration, tmp_path / "persistent analysis"


def test_runner_only_builds_literal_arguments_and_creates_nothing(runner_inputs):
    clip, calibration, destination = runner_inputs
    command = football_cv.build_run_command(
        Path(sys.executable), clip, calibration, destination, "play_123"
    )
    assert command[:3] == [str(Path(sys.executable).absolute()), "-I", "-c"]
    assert command[4:] == [str(clip), str(calibration), str(destination), "play_123"]
    assert "film; do-not-execute.mp4" not in command[3]
    assert "data_dir=sys.argv[3]" in command[3]
    assert "play_id=sys.argv[4]" in command[3]
    assert not destination.exists()


def test_runner_preserves_virtual_environment_interpreter_symlink(runner_inputs, tmp_path):
    clip, calibration, destination = runner_inputs
    interpreter = tmp_path / "isolated-venv" / "bin" / "python"
    interpreter.parent.mkdir(parents=True)
    interpreter.symlink_to(Path(sys.executable).resolve())
    command = football_cv.build_run_command(interpreter, clip, calibration, destination, "play")
    assert command[0] == str(interpreter)
    assert command[0] != str(interpreter.resolve())


@pytest.mark.parametrize("play_id", ["../clip", "-c", "clip/name", "", "x" * 101])
def test_runner_rejects_unsafe_play_ids(runner_inputs, play_id):
    clip, calibration, destination = runner_inputs
    with pytest.raises(MediaError) as error:
        football_cv.build_run_command(Path(sys.executable), clip, calibration, destination, play_id)
    assert error.value.code == "invalid_play_id"


def test_runner_rejects_nonfinite_calibration(runner_inputs):
    clip, calibration, destination = runner_inputs
    data = json.loads(calibration.read_text())
    data["points"][0]["px"] = math.inf
    calibration.write_text(json.dumps(data))
    with pytest.raises(MediaError) as error:
        football_cv.build_run_command(Path(sys.executable), clip, calibration, destination, "play")
    assert error.value.code == "invalid_calibration"
