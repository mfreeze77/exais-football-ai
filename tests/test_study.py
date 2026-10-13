"""Executable EFA-018..025 contracts, including destructive-operation guards."""

from __future__ import annotations

import base64
import sqlite3
import struct
import zlib
from uuid import uuid4

import pytest


def post(client, path, data, status=200):
    response = client.post("/api/study" + path, json=data)
    assert response.status_code == status, response.text
    return response.json()


def view(client, clip, title="Second cut", start=0.2, end=2.5, inherit=False):
    return post(
        client,
        f"/clips/{clip['id']}/views",
        {
            "title": title,
            "version": clip["version"],
            "start_seconds": start,
            "end_seconds": end,
            "inherit_labels": inherit,
        },
        201,
    )


def get(client, clip):
    return client.get("/api/clips/" + clip["id"]).json()


def selection(*clips):
    return [{"clip_id": c["id"], "version": c["version"]} for c in clips]


def test_batch_preview_is_read_only_and_add_replace_remove(client, import_clip):
    clip = import_clip(client)
    other = view(client, clip)
    catalog = client.app.state.catalog
    before_taxonomy = client.get("/api/taxonomy").json()
    body = {
        "clips": selection(clip, other),
        "changes": {
            "tags": {"mode": "add", "values": ["Tackle", "Résumé"]},
            "review_status": "review",
        },
    }
    plan = post(client, "/batches/preview", body)
    assert client.get("/api/taxonomy").json() == before_taxonomy
    assert get(client, clip) == clip
    assert plan["policy"] == "all_or_nothing"
    assert plan["clips"][0]["after"]["tags"] == ["résumé", "tackle"]
    receipt = post(client, "/batches", {**body, "preview_token": plan["preview_token"]}, 201)
    assert get(client, clip)["tags"] == ["résumé", "tackle"]
    assert get(client, other)["review_status"] == "review"
    assert receipt["clips"][0]["applied_version"] == 2
    for mode, values, expected in [
        ("remove", ["tackle"], ["résumé"]),
        ("replace", ["footwork"], ["footwork"]),
    ]:
        body = {
            "clips": selection(get(client, clip)),
            "changes": {"tags": {"mode": mode, "values": values}},
        }
        plan = post(client, "/batches/preview", body)
        post(client, "/batches", {**body, "preview_token": plan["preview_token"]}, 201)
        assert get(client, clip)["tags"] == expected
    with catalog.db.connection() as db:
        assert db.execute("SELECT count(*) FROM batch_edits").fetchone()[0] == 3


def test_batch_conflict_blocks_every_selected_clip(client, import_clip):
    clip = import_clip(client)
    other = view(client, clip)
    body = {"clips": selection(clip, other), "changes": {"favorite": True}}
    plan = post(client, "/batches/preview", body)
    client.patch("/api/clips/" + other["id"], json={"version": 1, "description": "Newer work"})
    result = post(client, "/batches", {**body, "preview_token": plan["preview_token"]}, 409)
    assert result["error"]["code"] == "batch_conflict"
    assert get(client, clip)["favorite"] is False
    assert get(client, other)["description"] == "Newer work"
    assert client.get("/api/study/batches").json() == []


def test_batch_transaction_rollback_on_second_patch(client, import_clip, monkeypatch):
    clip = import_clip(client)
    other = view(client, clip)
    body = {
        "clips": selection(clip, other),
        "changes": {"tags": {"mode": "add", "values": ["fresh"]}},
    }
    plan = post(client, "/batches/preview", body)
    catalog = client.app.state.catalog
    original = catalog._patch
    count = 0

    def fail(connection, key, patch):
        nonlocal count
        count += 1
        output = original(connection, key, patch)
        if count == 2:
            raise sqlite3.OperationalError("injected disk full")
        return output

    monkeypatch.setattr(catalog, "_patch", fail)
    post(client, "/batches", {**body, "preview_token": plan["preview_token"]}, 503)
    assert get(client, clip)["tags"] == get(client, other)["tags"] == []
    assert client.get("/api/taxonomy").json()["tags"] == []


def test_batch_undo_restores_only_changed_fields_and_conflicts(client, import_clip):
    clip = import_clip(client)
    client.post(f"/api/clips/{clip['id']}/notes", json={"body": "Keep this note", "at_seconds": 1})
    body = {"clips": selection(clip), "changes": {"tags": {"mode": "add", "values": ["pass"]}}}
    plan = post(client, "/batches/preview", body)
    receipt = post(client, "/batches", {**body, "preview_token": plan["preview_token"]}, 201)
    post(client, f"/batches/{receipt['id']}/undo", {})
    assert get(client, clip)["tags"] == []
    assert client.get(f"/api/clips/{clip['id']}/notes").json()[0]["body"] == "Keep this note"
    post(client, f"/batches/{receipt['id']}/undo", {}, 409)
    body = {"clips": selection(get(client, clip)), "changes": {"favorite": True}}
    plan = post(client, "/batches/preview", body)
    receipt = post(client, "/batches", {**body, "preview_token": plan["preview_token"]}, 201)
    updated = get(client, clip)
    client.patch(
        "/api/clips/" + clip["id"],
        json={"version": updated["version"], "description": "Newer description"},
    )
    result = post(client, f"/batches/{receipt['id']}/undo", {}, 409)
    assert result["error"]["code"] == "undo_conflict"
    assert get(client, clip)["description"] == "Newer description"


@pytest.mark.parametrize(
    "changes",
    [
        {"game_id": str(uuid4())},
        {"player_ids": [str(uuid4())]},
        {"source_path": "/etc/passwd"},
        {"tags": {"mode": "erase", "values": []}},
        {"archived": None},
        {},
    ],
)
def test_invalid_batch_changes_are_non_mutating(client, import_clip, changes):
    clip = import_clip(client)
    response = client.post(
        "/api/study/batches/preview", json={"clips": selection(clip), "changes": changes}
    )
    assert response.status_code in {404, 422}
    assert get(client, clip) == clip


def test_batch_does_not_archive_unselected_clips(client, import_clip):
    clip = import_clip(client)
    other = view(client, clip)
    body = {"clips": selection(clip), "changes": {"archived": True}}
    plan = post(client, "/batches/preview", body)
    post(client, "/batches", {**body, "preview_token": plan["preview_token"]}, 201)
    assert get(client, clip)["archived"]
    assert not get(client, other)["archived"]


def test_views_share_source_but_not_editorial_state(client, import_clip):
    clip = import_clip(client)
    response = client.patch(
        "/api/clips/" + clip["id"],
        json={"version": 1, "tags": ["first"], "start_seconds": 0.5, "end_seconds": 1.8},
    )
    clip = response.json()
    other = view(client, clip, inherit=True, start=0, end=3)
    assert other["source_asset_id"] == clip["source_asset_id"]
    assert other["source_sha256"] == clip["source_sha256"]
    assert other["tags"] == ["first"]
    client.patch(
        "/api/clips/" + other["id"], json={"version": 1, "tags": ["second"], "start_seconds": 1}
    )
    assert get(client, clip) == clip
    with client.app.state.catalog.db.connection() as db:
        assert db.execute("SELECT count(*) FROM assets").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM view_origins").fetchone()[0] == 1
    assert len(client.get(f"/api/study/clips/{clip['id']}/views").json()) == 2
    assert (
        client.get(f"/api/clips/{other['id']}/source").content
        == client.get(f"/api/clips/{clip['id']}/source").content
    )


def test_dynamic_search_and_reviewed_fixed_playlist(client, import_clip):
    clip = import_clip(client)
    other = view(client, clip)
    search = post(client, "/searches", {"title": "Favorites", "filters": {"favorite": True}}, 201)
    assert client.get(f"/api/study/searches/{search['id']}/results").json()["total"] == 0
    client.patch("/api/clips/" + clip["id"], json={"version": 1, "favorite": True})
    plan = post(client, f"/searches/{search['id']}/snapshot-preview", {})
    client.patch("/api/clips/" + other["id"], json={"version": 1, "favorite": True})
    post(
        client, f"/searches/{search['id']}/playlist", {"preview_token": plan["preview_token"]}, 409
    )
    plan = post(client, f"/searches/{search['id']}/snapshot-preview", {})
    playlist = post(
        client, f"/searches/{search['id']}/playlist", {"preview_token": plan["preview_token"]}, 201
    )
    client.patch("/api/clips/" + other["id"], json={"version": 2, "favorite": False})
    assert client.get(f"/api/study/searches/{search['id']}/results").json()["total"] == 1
    assert client.get("/api/playlists/" + playlist["id"]).json()["clip_ids"] == playlist["clip_ids"]
    assert len(playlist["clip_ids"]) == 2
    with client.app.state.catalog.db.connection(write=True) as db:
        db.execute("UPDATE saved_searches SET filter_version=2 WHERE id=?", (search["id"],))
    response = client.get(f"/api/study/searches/{search['id']}/results")
    assert response.status_code == 409


def test_session_frozen_order_progress_and_reset(client, import_clip):
    clip = import_clip(client)
    other = view(client, clip)
    playlist = client.post("/api/playlists", json={"title": "Study"}).json()
    client.put(
        f"/api/playlists/{playlist['id']}/clips", json={"clip_ids": [clip["id"], other["id"]]}
    )
    session = post(client, "/sessions", {"playlist_id": playlist["id"]}, 201)
    path = "/api/study/sessions/" + session["id"]
    for action in ["replay", "watch_again"]:
        response = client.patch(
            path,
            json={"version": session["version"], "index": 1, "at_seconds": 1.2, "action": action},
        )
        assert response.status_code == 200, response.text
        session = response.json()
    client.put(f"/api/playlists/{playlist['id']}/clips", json={"clip_ids": [clip["id"]]})
    # Reopening a fresh service against the persisted DB does not change progress.
    from exais_football.study import Study

    restored = Study(client.app.state.catalog).session(session["id"])
    assert len(restored["snapshot"]["clips"]) == 2
    assert restored["progress"]["replays"][other["id"]] == 1
    assert restored["progress"]["at_seconds"] == 1.2
    assert restored["progress"]["watch_again"] == [other["id"]]
    client.patch("/api/clips/" + other["id"], json={"version": 1, "archived": True})
    assert client.get(path).json()["availability"][1]["status"] == "archived"
    response = client.patch(
        path, json={"version": session["version"], "index": 1, "at_seconds": 1.2, "action": "reset"}
    )
    assert response.json()["progress"]["replays"] == {}
    assert get(client, clip)["review_status"] == "new"


def test_comparison_anchors_persist_without_rewriting_cuts(client, import_clip):
    clip = import_clip(client)
    other = view(client, clip)
    body = {
        "title": "First step",
        "left_clip_id": clip["id"],
        "right_clip_id": other["id"],
        "left_anchor": 0.5,
        "right_anchor": 1,
        "left_zoom": 1.5,
        "right_zoom": 2,
    }
    pair = post(client, "/comparisons", body, 201)
    assert pair["offset_seconds"] == 0.5
    assert pair["elapsed_start"] == -0.5
    assert pair["elapsed_end"] == 1.5
    assert not pair["stale"]
    assert get(client, clip) == clip and get(client, other) == other
    client.patch("/api/clips/" + other["id"], json={"version": 1, "description": "changed"})
    assert client.get("/api/study/comparisons/" + pair["id"]).json()["stale"]
    result = client.put(
        "/api/study/comparisons/" + pair["id"], json={**body, "version": 1, "left_anchor": 0.6}
    )
    assert result.status_code == 200 and result.json()["version"] == 2


def png(width=2, height=2):
    def chunk(kind, data):
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress((b"\0" + bytes([255, 0, 0, 255]) * width) * height))
        + chunk(b"IEND", b"")
    )


def test_drawings_revision_restore_and_snapshot(client, import_clip):
    clip = import_clip(client)
    path = f"/api/study/clips/{clip['id']}/drawings"
    drawing = {
        "id": str(uuid4()),
        "kind": "text",
        "points": [{"x": 0.2, "y": 0.3}],
        "start_seconds": 0,
        "end_seconds": 2,
        "text": "<script>alert(1)</script> — first step",
    }
    result = client.put(
        path, json={"version": 0, "source_sha256": clip["source_sha256"], "drawings": [drawing]}
    )
    assert result.status_code == 200, result.text
    saved = result.json()
    assert saved["drawings"][0]["text"] == drawing["text"]
    assert (
        client.put(
            path, json={"version": 0, "source_sha256": clip["source_sha256"], "drawings": []}
        ).status_code
        == 409
    )
    result = client.put(
        path, json={"version": 1, "source_sha256": clip["source_sha256"], "drawings": []}
    )
    assert result.json()["version"] == 2
    restored = post(
        client, f"/clips/{clip['id']}/drawings/restore", {"version": 2, "restore_revision": 1}
    )
    assert restored["version"] == 3 and restored["drawings"] == saved["drawings"]
    snap = post(
        client,
        f"/clips/{clip['id']}/snapshots",
        {
            "source_sha256": clip["source_sha256"],
            "drawing_revision": 3,
            "at_seconds": 1,
            "png_base64": base64.b64encode(png()).decode(),
        },
        201,
    )
    assert snap["manifest"]["source_asset_id"] == clip["source_asset_id"]
    assert snap["manifest"]["drawing_revision"] == 3
    assert get(client, clip) == clip


@pytest.mark.parametrize(
    "mutate",
    [
        lambda b: b[:-1],
        lambda b: b.replace(b"IDAT", b"XXXX", 1),
        lambda b: b"not a png",
        lambda b: png(4097, 1),
    ],
)
def test_snapshot_rejects_malformed_png(client, import_clip, mutate):
    clip = import_clip(client)
    post(
        client,
        f"/clips/{clip['id']}/snapshots",
        {
            "source_sha256": clip["source_sha256"],
            "drawing_revision": 0,
            "at_seconds": 1,
            "png_base64": base64.b64encode(mutate(png())).decode(),
        },
        422,
    )


def test_frame_index_and_exact_source_frame(client, import_clip, tmp_path):
    clip = import_clip(client)
    path = f"/clips/{clip['id']}/frame-index"
    assert client.get("/api/study" + path).json()["status"] == "needs_index"
    index = post(client, path, {})
    assert index["count"] == 75
    assert index["timestamps"][25] == pytest.approx(1, abs=1e-6)
    response = client.get(f"/api/study/clips/{clip['id']}/frames/25")
    assert response.status_code == 200, response.text
    from exais_football.workspace import validate_png

    assert validate_png(response.content)[0] == 320
    # Validate decoded frame identity against an independent FFmpeg extraction.
    import subprocess

    reference = tmp_path / "reference.png"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(client.app.state.catalog.source_path(clip["id"])),
            "-vf",
            r"select=eq(n\,25)",
            "-frames:v",
            "1",
            "-threads",
            "1",
            str(reference),
        ],
        check=True,
        capture_output=True,
        timeout=15,
    )
    actual = tmp_path / "actual.png"
    actual.write_bytes(response.content)

    def rgb(path):
        return subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-i",
                str(path),
                "-f",
                "rawvideo",
                "-pix_fmt",
                "rgb24",
                "-threads",
                "1",
                "pipe:1",
            ],
            check=True,
            capture_output=True,
            timeout=15,
        ).stdout

    assert rgb(actual) == rgb(reference)
    assert client.get(f"/api/study/clips/{clip['id']}/frames/99").status_code == 422
    # An index referring to another hash must regenerate, not silently be reused.
    import json

    cache = client.app.state.catalog.settings.path(
        f"cache/frames/{clip['source_sha256']}/index.json"
    )
    wrong = {**index, "source_sha256": "0" * 64}
    cache.write_text(json.dumps(wrong))
    assert post(client, path, {})["source_sha256"] == clip["source_sha256"]
