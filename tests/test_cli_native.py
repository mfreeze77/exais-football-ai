"""Operator CLI and native handoff boundary tests; live native qualification is separate."""

from __future__ import annotations

import json
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from exais_football import native_comparison
from exais_football.cli import main
from exais_football.service import file_sha256


def test_doctor_does_not_create_workspace(tmp_path, capsys):
    root = tmp_path / "absent café film"
    assert main(["doctor", "--data-dir", str(root)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["capabilities"]["football_cv"]["status"] == "not_configured"
    assert not root.exists()


def test_restore_activation_requires_preview_checksum(tmp_path):
    with pytest.raises(SystemExit) as error:
        main(["restore", str(tmp_path / "backup.zip"), str(tmp_path / "new"), "--execute"])
    assert error.value.code == 2
    assert not (tmp_path / "new").exists()


def test_offline_operator_commands_reject_active_workspace(client, capsys):
    root = client.app.state.catalog.settings.data_dir
    assert main(["backup", "--data-dir", str(root)]) == 1
    assert "Another Exais process" in capsys.readouterr().err


def test_package_inspection_error_is_actionable(tmp_path, capsys):
    assert main(["inspect-package", str(tmp_path / "missing.zip")]) == 1
    assert capsys.readouterr().err


def test_cv_configure_invalid_input_does_not_create_output(tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text('{"schema_version":999}')
    target = tmp_path / "configuration"
    assert main(["cv-configure", str(bad), "--data-dir", str(target)]) == 1
    assert not target.exists()
    assert "cv_configuration" in capsys.readouterr().err


def comparison(client, clip):
    response = client.post(
        "/api/study/comparisons",
        json={
            "title": "Unicode comparison – café",
            "left_clip_id": clip["id"],
            "right_clip_id": clip["id"],
            "left_anchor": 0.5,
            "right_anchor": 1.0,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_native_missing_platform_does_not_modify_sources(client, import_clip):
    clip = import_clip(client)
    pair = comparison(client, clip)
    response = client.post(f"/api/study/comparisons/{pair['id']}/kinovea/launch", json={})
    assert response.status_code == 503
    assert client.get("/api/clips/" + clip["id"]).json() == clip


def test_native_comparison_safe_args_copies_and_explicit_manual_sync(
    client, import_clip, tmp_path, monkeypatch
):
    clip = import_clip(client)
    catalog = client.app.state.catalog
    program = tmp_path / "Kinovea café folder" / "Kinovea.exe"
    program.parent.mkdir()
    program.write_bytes(b"not-executed-test-double")
    catalog.settings = replace(catalog.settings, kinovea_executable=str(program))
    monkeypatch.setattr(native_comparison, "capability", lambda _: {"available": True})
    monkeypatch.setattr(native_comparison, "executable_version", lambda _: (2025, 2, 0, 0))
    recorded = []

    class Process:
        def wait(self, timeout):
            raise subprocess.TimeoutExpired("native fixture", timeout)

    def launch(args, **kwargs):
        recorded.append((args, kwargs))
        assert kwargs["shell"] is False
        assert args[1] == "-video" and args[3] == "-video2"
        assert args[2] != args[4]
        for target in (Path(args[2]), Path(args[4])):
            assert file_sha256(target) == clip["source_sha256"]
            assert target.stat().st_ino != catalog.source_path(clip["id"]).stat().st_ino
        return Process()

    monkeypatch.setattr(native_comparison.subprocess, "Popen", launch)
    pair = comparison(client, clip)
    route = f"/api/study/comparisons/{pair['id']}/kinovea/launch"
    assert client.post(route, json={"switches": "-unsafe"}).status_code == 422
    result = client.post(route, json={})
    assert result.status_code == 200, result.text
    manifest = result.json()
    assert manifest["synchronization"] == "manual_native_anchors_required"
    assert manifest["comparison"]["offset_seconds"] == 0.5
    assert len(recorded) == 1
    assert client.get(manifest["receipt"]["download_url"]).status_code == 200
    assert file_sha256(catalog.source_path(clip["id"])) == clip["source_sha256"]


def test_native_unknown_version_blocks_before_handoff(client, import_clip, tmp_path, monkeypatch):
    clip = import_clip(client)
    catalog = client.app.state.catalog
    program = tmp_path / "unknown.exe"
    program.write_bytes(b"never execute")
    catalog.settings = replace(catalog.settings, kinovea_executable=str(program))
    monkeypatch.setattr(native_comparison, "capability", lambda _: {"available": True})
    monkeypatch.setattr(native_comparison, "executable_version", lambda _: (2027, 1, 0, 0))
    pair = comparison(client, clip)
    result = client.post(f"/api/study/comparisons/{pair['id']}/kinovea/launch", json={})
    assert result.status_code == 422
    assert result.json()["error"]["code"] == "kinovea_version_unsupported"
    assert not (catalog.settings.data_dir / "reviews/comparisons").exists()


def test_new_note_carries_view_and_asset_attribution(client, import_clip):
    clip = import_clip(client)
    response = client.post(
        f"/api/clips/{clip['id']}/notes", json={"body": "Study this", "at_seconds": 1}
    )
    note = response.json()
    assert note["clip_id"] == clip["id"] and note["source_asset_id"] == clip["source_asset_id"]
    assert client.get(f"/api/clips/{clip['id']}/notes").json()[0] == note
