"""Real filesystem handoff checks; Windows capability and process launch are stubbed."""

from __future__ import annotations

import errno
import os
from pathlib import Path

import pytest

from exais_football import app as app_module
from exais_football.service import file_sha256

IMPORTED_KVA = (
    b'<?xml version="1.0"?><KinoveaVideoAnalysis>'
    b"<FormatVersion>2.1</FormatVersion></KinoveaVideoAnalysis>"
)
NATIVE_KVA = IMPORTED_KVA + b"<!-- New edits saved in the native reviewer -->"


@pytest.fixture
def native_launch_calls(monkeypatch):
    calls = []
    monkeypatch.setattr(app_module.kinovea, "capability", lambda executable: {"available": True})
    monkeypatch.setattr(app_module.kinovea, "launch", lambda executable, video: calls.append(video))
    return calls


def import_annotations(client, clip_id):
    response = client.post(
        f"/api/clips/{clip_id}/kinovea/annotations",
        files={"file": ("review.kva", IMPORTED_KVA)},
    )
    assert response.status_code == 201, response.text


def test_native_handoff_copies_verified_bytes_and_retains_existing_work(
    client, import_clip, native_launch_calls
):
    clip = import_clip(client)
    catalog = client.app.state.catalog
    import_annotations(client, clip["id"])
    url = f"/api/clips/{clip['id']}/kinovea"
    response = client.post(url + "/launch", json={})
    assert response.status_code == 200, response.text
    video = catalog.settings.path(f"reviews/{clip['id']}/native/clip.mp4")
    annotation = video.with_suffix(".kva")
    original = catalog.source_path(clip["id"])
    preserved_annotation = catalog.settings.path(catalog.sidecar(clip["id"])["path"])
    assert native_launch_calls == [video]
    assert file_sha256(video) == clip["source_sha256"]
    assert not os.path.samefile(video, original)
    assert annotation.read_bytes() == IMPORTED_KVA
    assert not os.path.samefile(annotation, preserved_annotation)
    assert not list(video.parent.glob("*.tmp"))

    annotation.write_bytes(NATIVE_KVA)
    assert client.post(url + "/launch", json={}).status_code == 200
    assert annotation.read_bytes() == NATIVE_KVA
    assert client.get(url + "/annotations").content == IMPORTED_KVA
    assert file_sha256(original) == clip["source_sha256"]


@pytest.mark.parametrize("artifact", ["video", "annotations"])
def test_changed_staged_native_copy_is_not_published_or_launched(
    client, import_clip, native_launch_calls, monkeypatch, artifact
):
    clip = import_clip(client)
    catalog = client.app.state.catalog
    import_annotations(client, clip["id"])
    original = catalog.source_path(clip["id"])
    sidecar = catalog.settings.path(catalog.sidecar(clip["id"])["path"])
    changed_source = original if artifact == "video" else sidecar
    real_copy = app_module.shutil.copyfile

    def damaged_copy(source, destination):
        result = real_copy(source, destination)
        if Path(source) == changed_source:
            with Path(destination).open("ab") as stream:
                stream.write(b"damaged during copy")
        return result

    monkeypatch.setattr(app_module.shutil, "copyfile", damaged_copy)
    response = client.post(f"/api/clips/{clip['id']}/kinovea/launch", json={})
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "review_copy_verification_failed"
    workspace = catalog.settings.path(f"reviews/{clip['id']}/native")
    target = workspace / ("clip.mp4" if artifact == "video" else "clip.kva")
    assert not target.exists()
    assert not native_launch_calls
    assert not list(workspace.glob("*.tmp"))
    assert file_sha256(original) == clip["source_sha256"]
    assert sidecar.read_bytes() == IMPORTED_KVA


def test_native_sidecar_created_during_publication_is_never_overwritten(
    client, import_clip, native_launch_calls, monkeypatch
):
    clip = import_clip(client)
    catalog = client.app.state.catalog
    import_annotations(client, clip["id"])
    workspace = catalog.settings.path(f"reviews/{clip['id']}/native")
    annotation = workspace / "clip.kva"
    real_link = app_module.os.link

    def competing_native_save(source, destination):
        if Path(destination) == annotation:
            annotation.write_bytes(NATIVE_KVA)
        return real_link(source, destination)

    monkeypatch.setattr(app_module.os, "link", competing_native_save)
    response = client.post(f"/api/clips/{clip['id']}/kinovea/launch", json={})
    assert response.status_code == 200, response.text
    assert native_launch_calls == [workspace / "clip.mp4"]
    assert annotation.read_bytes() == NATIVE_KVA
    assert client.get(f"/api/clips/{clip['id']}/kinovea/annotations").content == IMPORTED_KVA
    assert not list(workspace.glob("*.tmp"))


def test_changed_native_video_created_during_publication_is_preserved_and_rejected(
    client, import_clip, native_launch_calls, monkeypatch
):
    clip = import_clip(client)
    catalog = client.app.state.catalog
    video = catalog.settings.path(f"reviews/{clip['id']}/native/clip.mp4")
    existing_work = b"A different native working video appeared"
    real_link = app_module.os.link

    def competing_video_write(source, destination):
        if Path(destination) == video:
            video.write_bytes(existing_work)
        return real_link(source, destination)

    monkeypatch.setattr(app_module.os, "link", competing_video_write)
    response = client.post(f"/api/clips/{clip['id']}/kinovea/launch", json={})
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "review_copy_changed"
    assert video.read_bytes() == existing_work
    assert not native_launch_calls
    assert not list(video.parent.glob("*.tmp"))
    assert file_sha256(catalog.source_path(clip["id"])) == clip["source_sha256"]


@pytest.mark.parametrize("failure_stage", ["copy", "publish"])
def test_native_io_failure_never_publishes_partial_video(
    client, import_clip, native_launch_calls, monkeypatch, failure_stage
):
    clip = import_clip(client)
    catalog = client.app.state.catalog

    def interrupted_copy(source, destination):
        Path(destination).write_bytes(b"partial copy")
        raise OSError(errno.ENOSPC, "Injected full disk")

    def unsupported_publication(source, destination):
        raise OSError(errno.EOPNOTSUPP, "Injected unsupported filesystem operation")

    if failure_stage == "copy":
        monkeypatch.setattr(app_module.shutil, "copyfile", interrupted_copy)
    else:
        monkeypatch.setattr(app_module.os, "link", unsupported_publication)
    response = client.post(f"/api/clips/{clip['id']}/kinovea/launch", json={})
    assert response.status_code == 503, response.text
    assert response.json()["error"]["code"] == "review_copy_failed"
    workspace = catalog.settings.path(f"reviews/{clip['id']}/native")
    assert not (workspace / "clip.mp4").exists()
    assert not list(workspace.glob("*.tmp"))
    assert not native_launch_calls
    assert file_sha256(catalog.source_path(clip["id"])) == clip["source_sha256"]
