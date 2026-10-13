"""The usable film workflow, persistent state, concurrency and trust boundaries."""

from __future__ import annotations

import hashlib
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from exais_football import jobs as jobs_module
from exais_football.app import create_app
from exais_football.config import Settings
from exais_football.integrations.football_cv import REVIEWED_UPSTREAM_REVISION
from exais_football.media import MediaError, probe_video
from exais_football.models import ClipPatch
from exais_football.service import Catalog, CatalogError, file_sha256


def wait_job(client, job_id):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in {"complete", "failed"}:
            return job
        time.sleep(0.05)
    pytest.fail("Export did not reach a terminal state within 30 seconds")


def test_complete_catalog_review_and_restore(client, import_clip, sample_video):
    game = client.post("/api/games", json={"title": "Week 3", "opponent": "Tigers"}).json()
    player = client.post(
        "/api/players", json={"name": "Alex", "jersey": "7", "position": "LB"}
    ).json()
    clip = import_clip(client, game_id=game["id"])
    clip_id = clip["id"]
    source_hash = hashlib.sha256(sample_video.read_bytes()).hexdigest()
    assert clip["source_sha256"] == source_hash and clip["source_duration"] == 3
    assert clip["version"] == 1 and clip["has_audio"]
    changes = {
        "version": 1,
        "title": "Read and fill",
        "phase": "defense",
        "review_status": "review",
        "tags": ["Run Fit", " run  fit ", "TACKLING"],
        "favorite": True,
        "player_ids": [player["id"]],
        "start_seconds": 0.44,
        "end_seconds": 1.76,
        "description": "First step toward the gap",
    }
    changed = client.patch(f"/api/clips/{clip_id}", json=changes)
    assert changed.status_code == 200, changed.text
    assert changed.json()["tags"] == ["run fit", "tackling"]
    assert changed.json()["duration"] == pytest.approx(1.32)
    note = client.post(
        f"/api/clips/{clip_id}/notes", json={"body": "Keep shoulders square", "at_seconds": 1.2}
    )
    assert note.status_code == 201
    found = client.get(
        "/api/clips",
        params={
            "q": "shoulders",
            "game_id": game["id"],
            "player_id": player["id"],
            "tag": "Run Fit",
            "phase": "defense",
            "favorite": "true",
            "review_status": "review",
        },
    ).json()
    assert found["total"] == 1 and found["items"][0]["id"] == clip_id
    assert client.get("/api/clips", params={"q": "%' OR 1=1 --"}).json()["total"] == 0
    assert client.get("/api/tags").json() == [
        {"name": "run fit", "count": 1},
        {"name": "tackling", "count": 1},
    ]
    stale = client.patch(f"/api/clips/{clip_id}", json={"version": 1, "title": "Stale"})
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "version_conflict"
    history = client.get(f"/api/clips/{clip_id}/history").json()
    assert [item["version"] for item in history] == [2, 1]
    restored = client.post(
        f"/api/clips/{clip_id}/restore", json={"version": 1, "current_version": 2}
    ).json()
    assert restored["version"] == 3 and restored["end_seconds"] == 3
    assert restored["tags"] == [] and not restored["favorite"]
    assert len(client.get(f"/api/clips/{clip_id}/notes").json()) == 1
    archived = client.patch(f"/api/clips/{clip_id}", json={"version": 3, "archived": True})
    assert archived.status_code == 200
    assert client.get("/api/clips").json()["total"] == 0
    assert client.get("/api/clips?archived=true").json()["total"] == 1
    assert client.get("/api/stats").json()["clips"] == 0
    assert file_sha256(client.app.state.catalog.source_path(clip_id)) == source_hash


def test_stream_range_head_and_thumbnails(client, import_clip, sample_video):
    clip = import_clip(client)
    original = sample_video.read_bytes()
    response = client.get(clip["source_url"], headers={"Range": "bytes=0-31"})
    assert response.status_code == 206 and response.content == original[:32]
    assert response.headers["content-range"] == f"bytes 0-31/{len(original)}"
    assert client.get(clip["source_url"], headers={"Range": "bytes=-16"}).content == original[-16:]
    assert client.get(clip["source_url"], headers={"Range": "bytes=99999999-"}).status_code == 416
    head = client.head(clip["source_url"])
    assert head.status_code == 200 and not head.content
    assert int(head.headers["content-length"]) == len(original)
    preview = client.get(clip["thumbnail_url"])
    assert preview.status_code == 200 and preview.content.startswith(b"\xff\xd8")


def test_duplicate_and_invalid_imports_leave_original_intact(client, import_clip, sample_video):
    clip = import_clip(client, title="Original title")
    duplicate = client.post(
        "/api/imports",
        files={"file": ("renamed.mp4", sample_video.read_bytes())},
        data={"title": "A new title"},
    )
    assert duplicate.json()["duplicate"] is True
    assert duplicate.json()["clip"]["id"] == clip["id"]
    assert duplicate.json()["clip"]["title"] == "Original title"
    assert client.get("/api/stats").json()["clips"] == 1
    for filename, content, expected in [
        ("bad.mp4", b"this is not a video", 422),
        ("payload.exe", sample_video.read_bytes(), 415),
        ("empty.mp4", b"", 413),
    ]:
        response = client.post("/api/imports", files={"file": (filename, content)})
        assert response.status_code == expected, response.text
    assert not list(client.app.state.catalog.settings.path("staging").iterdir())
    assert len(list(client.app.state.catalog.settings.path("sources").rglob("*.mp4"))) == 1


@pytest.mark.parametrize(
    "change",
    [
        {"start_seconds": 2, "end_seconds": 1},
        {"end_seconds": 20},
        {"start_seconds": -1},
        {"source_sha256": "0" * 64},
        {"title": ""},
        {"title": None},
        {"tags": [""]},
        {"phase": "made_up"},
        {"player_ids": [str(uuid4())]},
        {"game_id": str(uuid4())},
    ],
)
def test_invalid_edits_are_atomic(client, import_clip, change):
    clip = import_clip(client)
    response = client.patch(f"/api/clips/{clip['id']}", json={"version": 1, **change})
    assert response.status_code in {404, 422}, response.text
    persisted = client.get(f"/api/clips/{clip['id']}").json()
    assert persisted["version"] == 1 and persisted["end_seconds"] == 3
    assert len(client.get(f"/api/clips/{clip['id']}/history").json()) == 1


def test_two_writers_cannot_silently_overwrite(client, import_clip):
    clip = import_clip(client)
    catalog = client.app.state.catalog
    barrier = threading.Barrier(2)

    def save(title):
        barrier.wait(timeout=5)
        try:
            catalog.patch_clip(clip["id"], ClipPatch(version=1, title=title))
            return 200
        except CatalogError as error:
            return error.status

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(save, ["Writer one", "Writer two"]))
    assert sorted(results) == [200, 409]
    assert catalog.get_clip(clip["id"])["version"] == 2


def test_note_bounds_and_playlist_relationships(client, import_clip):
    clip = import_clip(client)
    clip_id = clip["id"]
    assert (
        client.post(
            f"/api/clips/{clip_id}/notes", json={"body": "late", "at_seconds": 4}
        ).status_code
        == 422
    )
    note = client.post(f"/api/clips/{clip_id}/notes", json={"body": "General note"}).json()
    assert client.delete(f"/api/clips/{uuid4()}/notes/{note['id']}").status_code == 404
    assert len(client.get(f"/api/clips/{clip_id}/notes").json()) == 1
    playlist = client.post("/api/playlists", json={"title": "Run fits"}).json()
    url = f"/api/playlists/{playlist['id']}/clips"
    assert client.put(url, json={"clip_ids": [clip_id]}).json()["clip_ids"] == [clip_id]
    assert client.put(url, json={"clip_ids": [clip_id, clip_id]}).status_code == 422
    assert client.put(url, json={"clip_ids": [str(uuid4())]}).status_code == 404
    assert client.get(f"/api/playlists/{playlist['id']}").json()["clip_ids"] == [clip_id]
    assert client.put(url, json={"clip_ids": []}).json()["clips"] == []
    assert client.delete(f"/api/clips/{clip_id}/notes/{note['id']}").status_code == 204


def test_real_export_uses_frozen_edit_and_preserves_audio(client, import_clip, monkeypatch):
    clip = import_clip(client)
    clip_id = clip["id"]
    client.patch(
        f"/api/clips/{clip_id}", json={"version": 1, "start_seconds": 0.48, "end_seconds": 1.84}
    )
    ready, proceed = threading.Event(), threading.Event()
    real_export = jobs_module.export_trim

    def paused_export(*args):
        ready.set()
        assert proceed.wait(timeout=10)
        return real_export(*args)

    monkeypatch.setattr(jobs_module, "export_trim", paused_export)
    response = client.post(f"/api/clips/{clip_id}/exports", json={})
    assert response.status_code == 202
    job = response.json()
    assert ready.wait(timeout=10)
    try:
        assert client.get(f"/api/jobs/{job['id']}/file").status_code == 409
        changed = client.patch(
            f"/api/clips/{clip_id}",
            json={
                "version": 2,
                "start_seconds": 0,
                "end_seconds": 3,
            },
        )
        assert changed.status_code == 200
    finally:
        proceed.set()
    completed = wait_job(client, job["id"])
    assert completed["status"] == "complete", completed
    assert completed["clip_version"] == 2 and completed["provenance"]["start_seconds"] == 0.48
    exported = client.app.state.jobs.file(job["id"])
    probe = probe_video(exported)
    assert probe["duration"] == pytest.approx(1.36, abs=0.08) and probe["has_audio"]
    assert client.get(completed["download_url"]).content == exported.read_bytes()
    assert file_sha256(client.app.state.catalog.source_path(clip_id)) == clip["source_sha256"]
    assert completed["result"]["source_sha256"] == clip["source_sha256"]


def test_failed_exports_report_a_reason_and_no_download(client, import_clip, monkeypatch):
    clip = import_clip(client)

    def fail(*args):
        raise MediaError("encoder_unavailable", "The required encoder is unavailable")

    monkeypatch.setattr(jobs_module, "export_trim", fail)
    job = client.post(f"/api/clips/{clip['id']}/exports", json={}).json()
    failed = wait_job(client, job["id"])
    assert failed["status"] == "failed" and failed["error"]["code"] == "encoder_unavailable"
    assert failed["download_url"] is None
    assert client.get(f"/api/jobs/{job['id']}/file").status_code == 409


def test_source_change_between_precheck_and_real_render_is_not_published(
    client, import_clip, monkeypatch
):
    clip = import_clip(client)
    real_export = jobs_module.export_trim

    def changed_after_precheck(source, *args):
        # Valid trailing MP4 bytes simulate an external modification in the
        # interval between the job's check and the renderer opening its input.
        with source.open("ab") as stream:
            stream.write(b"exais-test-source-was-modified")
        return real_export(source, *args)

    monkeypatch.setattr(jobs_module, "export_trim", changed_after_precheck)
    job = client.post(f"/api/clips/{clip['id']}/exports", json={}).json()
    result = wait_job(client, job["id"])
    assert result["status"] == "failed" and result["error"]["code"] == "source_changed"
    assert result["download_url"] is None
    assert not client.app.state.catalog.settings.path(f"exports/{job['id']}.mp4").exists()


def test_untrusted_filename_is_metadata_only(client, sample_video, tmp_path):
    filename = r"../../outside\$(touch forbidden);clip.mp4"
    response = client.post("/api/imports", files={"file": (filename, sample_video.read_bytes())})
    assert response.status_code == 201, response.text
    clip = response.json()["clip"]
    assert clip["original_filename"] == "$(touch forbidden);clip.mp4"
    source = client.app.state.catalog.source_path(clip["id"])
    assert source.name == clip["source_sha256"] + ".mp4"
    assert source.is_relative_to(client.app.state.catalog.settings.data_dir)
    assert not (tmp_path / "forbidden").exists()


def test_note_timestamps_cover_boundaries_and_reject_nonfinite(client, import_clip):
    clip = import_clip(client)
    url = f"/api/clips/{clip['id']}/notes"
    for boundary in (0, clip["source_duration"]):
        assert (
            client.post(url, json={"body": "Boundary", "at_seconds": boundary}).status_code == 201
        )
    for literal in ("NaN", "Infinity", "-Infinity"):
        response = client.post(
            url,
            content='{"body":"Invalid time","at_seconds":' + literal + "}",
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 422
    assert len(client.get(url).json()) == 2


def test_analysis_requires_matching_source_and_separate_human_review(client, import_clip):
    clip = import_clip(client)
    url = f"/api/clips/{clip['id']}/analysis"
    bundle = {
        "source_sha256": clip["source_sha256"],
        "upstream_revision": REVIEWED_UPSTREAM_REVISION,
        "positions": {"1": "LB", "2": "QB"},
    }
    assert client.post(url, json={**bundle, "source_sha256": "0" * 64}).status_code == 422
    assert client.post(url, json={**bundle, "positions": {"Alex": "LB"}}).status_code == 422
    result = client.post(url, json=bundle)
    assert result.status_code == 201, result.text
    result = result.json()
    assert (
        result["status"] == "unreviewed"
        and result["payload"]["identity_resolution"] == "not_provided"
    )
    accepted = client.patch(f"{url}/{result['id']}", json={"status": "accepted"}).json()
    assert accepted["status"] == "accepted" and len(accepted["reviews"]) == 1
    persisted = client.get(f"/api/clips/{clip['id']}").json()
    assert persisted["tags"] == [] and persisted["player_ids"] == [] and persisted["version"] == 1
    assert accepted["payload"] == result["payload"]
    wrong_clip_url = f"/api/clips/{uuid4()}/analysis/{result['id']}"
    assert client.patch(wrong_clip_url, json={"status": "rejected"}).status_code == 404


def test_kva_sidecar_is_preserved_but_not_claimed_as_native_verified(client, import_clip):
    clip = import_clip(client)
    url = f"/api/clips/{clip['id']}/kinovea"
    content = (
        b'<?xml version="1.0"?><KinoveaVideoAnalysis>'
        b"<FormatVersion>2.1</FormatVersion></KinoveaVideoAnalysis>"
    )
    saved = client.post(url + "/annotations", files={"file": ("review.kva", content)})
    assert saved.status_code == 201, saved.text
    assert saved.json()["sha256"] == hashlib.sha256(content).hexdigest()
    assert client.get(url + "/annotations").content == content
    assert client.get(url).json()["has_annotations"] is True
    unsafe = (
        b'<!DOCTYPE x [<!ENTITY secret SYSTEM "file:///etc/passwd">]>'
        b"<KinoveaVideoAnalysis>&secret;</KinoveaVideoAnalysis>"
    )
    assert (
        client.post(url + "/annotations", files={"file": ("unsafe.kva", unsafe)}).status_code == 422
    )
    assert client.get(url + "/annotations").content == content
    assert client.post(url + "/launch", json={}).status_code == 503


def test_catalog_survives_restart_and_interrupted_job_is_honest(tmp_path, import_clip):
    settings = Settings(data_dir=tmp_path / "restart")
    app = create_app(settings)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        clip = import_clip(client)
        client.post(f"/api/clips/{clip['id']}/notes", json={"body": "Persistent note"})
    catalog = app.state.catalog
    job_id = str(uuid4())
    with catalog.db.connection(write=True) as connection:
        connection.execute(
            "INSERT INTO jobs(id,clip_id,kind,status,clip_version,snapshot,created_at,updated_at) "
            "VALUES(?,?,'trim','running',1,?,'before','before')",
            (job_id, clip["id"], json.dumps(clip)),
        )
    # Restore the state of a process interrupted with a running job, then reopen.
    reopened = create_app(catalog.settings)
    with TestClient(reopened, base_url="http://127.0.0.1") as second:
        assert second.get(f"/api/clips/{clip['id']}").json() == clip
        assert len(second.get(f"/api/clips/{clip['id']}/notes").json()) == 1
        job = second.get(f"/api/jobs/{job_id}").json()
        assert job["status"] == "failed" and job["error"]["code"] == "interrupted"


def test_second_running_instance_is_rejected_without_interrupting_owner(client):
    competing = create_app(client.app.state.catalog.settings)
    with pytest.raises(RuntimeError, match="Another Exais process"):
        with TestClient(competing, base_url="http://127.0.0.1"):
            pytest.fail("A second process acquired the active data directory")
    assert client.get("/api/health").status_code == 200


def test_local_origin_body_limits_and_paths(client, tmp_path, sample_video):
    assert client.get("/api/health", headers={"Host": "attacker.example"}).status_code == 403
    assert (
        client.post(
            "/api/games",
            json={"title": "x", "opponent": "y"},
            headers={"Origin": "https://attacker.example"},
        ).status_code
        == 403
    )
    assert client.get("/api/stats", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert client.get("/api/stats", headers={"Origin": "http://127.0.0.1"}).status_code == 200
    assert client.post("/api/games", content=b"x" * (256 * 1024 + 1)).status_code == 413
    # No Content-Length: the receive wrapper must still enforce the body limit.
    chunked = client.post(
        "/api/games",
        content=iter([b"x" * 200000, b"y" * 100000]),
        headers={"Content-Type": "application/json"},
    )
    assert chunked.status_code == 413, chunked.text
    assert client.get("/api/clips/not-a-uuid/source").status_code == 422
    assert client.get("/static/..%2F..%2FLICENSE").status_code == 404
    with pytest.raises(ValueError):
        client.app.state.catalog.settings.path("../outside")
    small_app = create_app(Settings(data_dir=tmp_path / "small", max_upload_bytes=1024))
    with TestClient(small_app, base_url="http://127.0.0.1") as small:
        rejected = small.post(
            "/api/imports", files={"file": ("big.mp4", sample_video.read_bytes())}
        )
        assert rejected.status_code == 413
        assert small.get("/api/stats").json()["clips"] == 0


def test_future_database_schema_is_not_silently_downgraded(tmp_path):
    settings = Settings(data_dir=tmp_path / "future")
    catalog = Catalog(settings)
    with catalog.db.connection(write=True) as connection:
        connection.execute("PRAGMA user_version=999")
    with pytest.raises(RuntimeError, match="newer schema"):
        Catalog(settings)
