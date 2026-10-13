"""Real backup/restore/export drills and hostile archive/cleanup regression cases."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import stat
import time
import zipfile

import pytest

from exais_football.config import Settings
from exais_football.portable import create_package, freeze_selection
from exais_football.recovery import create_backup, inspect_package, restore_backup, verify_catalog
from exais_football.service import Catalog, CatalogError, file_sha256
from exais_football.storage import LeasedFileResponse, cleanup, preview_cleanup, report
from exais_football.work import WorkJobs


def artifact_path(catalog, record):
    with catalog.db.connection() as db:
        relative = db.execute(
            "SELECT path FROM artifact_records WHERE id=?", (record["id"],)
        ).fetchone()[0]
    return catalog.settings.path(relative)


def wait_job(client, record):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        record = client.get("/api/work/" + record["id"]).json()
        if record["status"] not in {"queued", "running"}:
            return record
        time.sleep(0.02)
    pytest.fail("Work queue did not terminate")


def test_backup_restore_real_retrieval_and_audio_export(client, import_clip, tmp_path):
    clip = import_clip(client)
    client.patch(
        "/api/clips/" + clip["id"],
        json={"version": clip["version"], "tags": ["tackling", "footwork"]},
    )
    client.post(
        "/api/clips/" + clip["id"] + "/notes",
        json={"body": "Keep the feet moving", "at_seconds": 1.0},
    )
    catalog = client.app.state.catalog
    record = create_backup(catalog)
    source = artifact_path(catalog, record)
    checked = inspect_package(source)
    assert checked["status"] == "verified"
    destination = tmp_path / "Restored film – café"
    preview = restore_backup(source, destination)
    assert not preview["activated"] and not destination.exists()
    restored = restore_backup(
        source, destination, dry_run=False, expected_archive_sha256=preview["archive_sha256"]
    )
    assert restored["activated"] and not (destination / ".restore-in-progress").exists()
    recovered = Catalog(Settings(data_dir=destination))
    assert recovered.get_clip(clip["id"])["tags"] == ["footwork", "tackling"]
    assert recovered.notes(clip["id"])[0]["body"] == "Keep the feet moving"
    assert file_sha256(recovered.source_path(clip["id"])) == clip["source_sha256"]
    from exais_football.media import export_trim, probe_video

    output = tmp_path / "restored-export.mp4"
    result = export_trim(recovered.source_path(clip["id"]), output, 0.25, 2.25)
    assert result["source_sha256"] == clip["source_sha256"]
    assert probe_video(output)["has_audio"]
    assert abs(probe_video(output)["duration"] - 2) < 0.15
    with pytest.raises(CatalogError, match="nonexistent"):
        restore_backup(source, destination, dry_run=False)
    assert file_sha256(recovered.source_path(clip["id"])) == clip["source_sha256"]


def test_backup_checksum_failure_leaves_no_success_artifact(client, import_clip):
    clip = import_clip(client)
    catalog = client.app.state.catalog
    catalog.source_path(clip["id"]).write_bytes(b"changed original")
    with pytest.raises(CatalogError, match="checksum"):
        create_backup(catalog)
    assert not list(catalog.settings.path("backups").glob("*"))


def archive(tmp_path, members, kind="exais-full-library"):
    path = tmp_path / "hostile.zip"
    with zipfile.ZipFile(path, "w") as z:
        manifest = {"schema_version": 1, "kind": kind, "files": []}
        for name, value in members:
            z.writestr(name, value)
            manifest["files"].append(
                {"path": name, "size": len(value), "sha256": hashlib.sha256(value).hexdigest()}
            )
        z.writestr("manifest.json", json.dumps(manifest))
    return path


@pytest.mark.parametrize(
    "name",
    [
        "../evil",
        "/absolute",
        "C:/windows",
        "a\\b",
        "a/../b",
        "a//b",
        "CON.txt",
        "aux",
        "a.",
        "b /c",
        "x\x01",
    ],
)
def test_archive_unsafe_paths_rejected(tmp_path, name):
    with pytest.raises(CatalogError):
        inspect_package(archive(tmp_path, [(name, b"payload")]))
    assert not (tmp_path.parent / "evil").exists()


def test_archive_case_unicode_collision_symlink_and_size_limits(tmp_path):
    for names in [("a", "A"), ("café", "cafe\u0301")]:
        with pytest.raises(CatalogError, match="collid"):
            inspect_package(archive(tmp_path, [(n, b"data") for n in names]))
    path = archive(tmp_path, [("payload", b"x")])
    with pytest.raises(CatalogError, match="total"):
        inspect_package(path, max_total_bytes=1)
    with zipfile.ZipFile(path, "w") as z:
        info = zipfile.ZipInfo("link")
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        z.writestr(info, "/etc/passwd")
        z.writestr("manifest.json", "{}")
    with pytest.raises(CatalogError, match="links"):
        inspect_package(path)


def test_corrupt_or_unknown_manifest_never_activates(tmp_path):
    path = archive(tmp_path, [("catalog.sqlite3", b"not a database")])
    dest = tmp_path / "new"
    with pytest.raises((CatalogError, sqlite3.DatabaseError)):
        restore_backup(path, dest, dry_run=False)
    assert not dest.exists()
    with zipfile.ZipFile(path, "a") as z:
        z.writestr("extra", "unlisted")
    with pytest.raises(CatalogError):
        inspect_package(path)


def test_restore_rejects_newer_schema_and_unexpected_triggers(client, tmp_path):
    catalog = client.app.state.catalog
    from exais_football.recovery import snapshot_database

    path = tmp_path / "db.sqlite3"
    snapshot_database(catalog, path)
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA user_version=999")
    with pytest.raises(CatalogError, match="compatible"):
        verify_catalog(path)
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA user_version=3")
        db.execute("CREATE TRIGGER unexpected AFTER INSERT ON games BEGIN SELECT 1; END;")
    with pytest.raises(CatalogError, match="unexpected"):
        verify_catalog(path)


def test_restore_marker_blocks_startup(tmp_path):
    root = tmp_path / "interrupted"
    root.mkdir()
    (root / ".restore-in-progress").write_text("{}")
    with pytest.raises(RuntimeError, match="interrupted restore"):
        Catalog(Settings(data_dir=root))
    assert not (root / "catalog.sqlite3").exists()


def test_restore_racing_destination_is_not_replaced(client, tmp_path, monkeypatch):
    catalog = client.app.state.catalog
    record = create_backup(catalog)
    source = artifact_path(catalog, record)
    dest = tmp_path / "race"
    import exais_football.recovery as recovery

    original = recovery.verify_catalog

    def race(db):
        result = original(db)
        dest.mkdir()
        (dest / "owner.txt").write_text("keep")
        return result

    monkeypatch.setattr(recovery, "verify_catalog", race)
    with pytest.raises(CatalogError, match="created"):
        restore_backup(source, dest, dry_run=False)
    assert (dest / "owner.txt").read_text() == "keep"
    assert not (dest / "catalog.sqlite3").exists()


def test_portable_frozen_selection_csv_safe_and_no_unselected_media(client, import_clip):
    clip = import_clip(client)
    catalog = client.app.state.catalog
    clip = client.patch(
        "/api/clips/" + clip["id"],
        json={"version": 1, "title": "=SUM(1,2)", "tags": ["café", "route, break"]},
    ).json()
    snapshot = freeze_selection(catalog, [{"clip_id": clip["id"], "version": clip["version"]}])
    client.patch(
        "/api/clips/" + clip["id"], json={"version": clip["version"], "title": "new title"}
    )
    record = create_package(catalog, snapshot)
    path = artifact_path(catalog, record)
    assert inspect_package(path)["manifest"]["metadata_only"]
    with zipfile.ZipFile(path) as z:
        assert set(z.namelist()) == {"metadata.json", "clips.csv", "manifest.json"}
        metadata = json.loads(z.read("metadata.json"))
        assert metadata["clips"][0]["clip"]["title"] == "=SUM(1,2)"
        assert metadata["clips"][0]["clip"]["tags"] == ["café", "route, break"]
        assert "'=SUM" in z.read("clips.csv").decode("utf-8-sig")
        assert str(catalog.settings.data_dir) not in z.read("metadata.json").decode()


def test_portable_media_really_retains_audio(client, import_clip):
    clip = import_clip(client)
    catalog = client.app.state.catalog
    snapshot = freeze_selection(catalog, [{"clip_id": clip["id"], "version": 1}], True)
    record = create_package(catalog, snapshot)
    with zipfile.ZipFile(artifact_path(catalog, record)) as z:
        metadata = json.loads(z.read("metadata.json"))
        assert metadata["clips"][0]["export"]["has_audio"]
        assert len([n for n in z.namelist() if n.endswith(".mp4")]) == 1


def test_cleanup_protects_sources_downloads_and_changed_preview(client, import_clip):
    clip = import_clip(client)
    catalog = client.app.state.catalog
    inventory = report(catalog)
    ids = [i["id"] for i in inventory["candidates"]]
    assert ids and all(i["kind"] == "thumbnails" for i in inventory["candidates"])
    plan = preview_cleanup(catalog, ids)
    path = catalog.settings.path(plan["files"][0]["path"])
    path.write_bytes(path.read_bytes() + b"changed")
    with pytest.raises(CatalogError, match="changed"):
        cleanup(catalog, ids, plan["preview_token"])
    plan = preview_cleanup(catalog, ids)
    result = cleanup(catalog, ids, plan["preview_token"])
    assert result["complete"] and result["recovered_bytes"] > 0
    assert file_sha256(catalog.source_path(clip["id"])) == clip["source_sha256"]
    with pytest.raises(CatalogError):
        cleanup(
            catalog,
            [hashlib.sha256(catalog.asset(clip["id"])["path"].encode()).hexdigest()],
            "0" * 64,
        )


def test_cleanup_lease_excludes_file(client, import_clip):
    import asyncio

    import_clip(client)
    catalog = client.app.state.catalog
    item = report(catalog)["candidates"][0]
    path = catalog.settings.path(item["path"])
    response = LeasedFileResponse(path)
    try:
        assert report(catalog)["candidates"][0]["excluded_reason"] == "active_download"
        with pytest.raises(CatalogError):
            preview_cleanup(catalog, [item["id"]])
    finally:
        # Exercise release even when the actual response send fails.
        async def fail(message):
            raise RuntimeError("disconnect")

        async def receive():
            return {"type": "http.disconnect"}

        with pytest.raises(RuntimeError):
            asyncio.run(response({"type": "http", "method": "GET", "headers": []}, receive, fail))
    assert preview_cleanup(catalog, [item["id"]])["total_bytes"] > 0


def test_backup_api_work_and_verified_download(client, import_clip):
    import_clip(client)
    result = client.post("/api/backups", json={})
    assert result.status_code == 202
    job = wait_job(client, result.json())
    assert job["status"] == "complete", job
    record = job["result"]
    result = client.get(record["download_url"])
    assert result.status_code == 200
    assert (
        client.post("/api/artifacts/" + record["id"] + "/inspect", json={}).json()["status"]
        == "verified"
    )
    assert client.get("/api/integrity").json()["status"] == "ok"


def test_durable_work_cancel_retry_frozen_attempt_and_interruption(tmp_path):
    catalog = Catalog(Settings(data_dir=tmp_path / "jobs"))
    jobs = WorkJobs(catalog)
    jobs.register("fake", lambda snapshot, cancel, stage: {"input": snapshot})
    a = jobs.enqueue("fake", {"x": 1})
    jobs.cancel(a["id"])
    assert not jobs.run_once()
    b = jobs.retry(a["id"])
    assert b["id"] != a["id"] and b["attempt"] == 2
    assert jobs.run_once()
    assert jobs.get(b["id"])["result"] == {"input": {"x": 1}}
    with pytest.raises(CatalogError):
        jobs.retry(b["id"])
    c = jobs.enqueue("fake", {"x": 2})
    jobs.start()
    jobs.stop()
    assert jobs.get(c["id"])["status"] == "interrupted"
    assert jobs.get(a["id"])["status"] == "cancelled"


def test_work_failure_is_not_success_and_queue_is_bounded(tmp_path):
    catalog = Catalog(Settings(data_dir=tmp_path / "jobs", max_pending_jobs=1))
    jobs = WorkJobs(catalog)

    def fail(snapshot, cancel, stage):
        raise OSError("disk full")

    jobs.register("fake", fail)
    a = jobs.enqueue("fake", {})
    with pytest.raises(CatalogError, match="full"):
        jobs.enqueue("fake", {})
    jobs.run_once()
    assert jobs.get(a["id"])["status"] == "failed"
    assert jobs.get(a["id"])["result"] is None
