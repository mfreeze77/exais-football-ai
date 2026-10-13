"""EFA-017 acceptance: routes, rollback, migration, aliases, presets, and evidence."""

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from exais_football.db import SCHEMA, Database
from exais_football.models import ClipPatch
from exais_football.service import Catalog, file_sha256
from exais_football.tagging import DEFAULT_GROUPS


@pytest.fixture
def film(client):
    """Metadata fixtures, not playable; real-video provenance has a separate test."""
    catalog = client.app.state.catalog

    def add(tags=(), archived=False):
        clip_id, asset_id = str(uuid4()), str(uuid4())
        with catalog.db.connection(write=True) as connection:
            connection.execute(
                "INSERT INTO assets VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    asset_id,
                    asset_id,
                    f"sources/{asset_id}.mp4",
                    "fixture.mp4",
                    10,
                    10,
                    320,
                    180,
                    25,
                    1,
                    "2026-01-01",
                ),
            )
            connection.execute(
                """INSERT INTO clips(id,asset_id,title,start_seconds,end_seconds,created_at,
                updated_at) VALUES(?,?,?,0,10,'2026-01-01','2026-01-01')""",
                (clip_id, asset_id, f"Play {clip_id}"),
            )
            catalog._record_revision(connection, clip_id)
        return catalog.patch_clip(clip_id, ClipPatch(version=1, tags=list(tags), archived=archived))

    return add


def snapshot(client):
    response = client.get("/api/taxonomy")
    assert response.status_code == 200, response.text
    return response.json()


def term(client, name):
    return next(t for t in snapshot(client)["tags"] if t["name"] == name)


def write(client, route, body=None, method="post", status=200):
    data = {"expected_revision": snapshot(client)["revision"], **(body or {})}
    response = getattr(client, method)("/api/taxonomy/" + route, json=data)
    assert response.status_code == status, response.text
    return response.json()


def preview(client, source, target):
    response = client.post(
        "/api/taxonomy/changes/preview",
        json={
            "tag_id": term(client, source)["id"],
            "new_name": target,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def apply(client, plan, status=200):
    response = client.post(
        "/api/taxonomy/changes/apply",
        json={
            "tag_id": plan["source"]["id"],
            "new_name": plan["requested_name"],
            "preview_token": plan["preview_token"],
        },
    )
    assert response.status_code == status, response.text
    return response.json()


def get_clip(client, clip):
    return client.get(f"/api/clips/{clip['id']}").json()


def test_editable_groups_and_unfamiliar_tags_survive_group_deletion(client, film):
    clip = film(["  Custom   Coach Label  ", "unfamiliar/é"])
    state = snapshot(client)
    assert {g["name"] for g in state["groups"]} == set(DEFAULT_GROUPS)
    assert all(t["group_id"] is None for t in state["tags"])
    write(client, "groups", {"name": "My vocabulary", "description": "Owner supplied"}, status=201)
    group = next(g for g in snapshot(client)["groups"] if g["name"] == "My vocabulary")
    write(client, f"groups/{group['id']}", {"name": "Our skills"}, method="put")
    tag = term(client, "custom coach label")
    write(client, f"tags/{tag['id']}", {"group_id": group["id"]}, method="put")
    assert term(client, tag["name"])["group_id"] == group["id"]
    result = client.delete(
        f"/api/taxonomy/groups/{group['id']}",
        params={
            "expected_revision": snapshot(client)["revision"],
        },
    )
    assert result.status_code == 200
    assert term(client, tag["name"])["group_id"] is None
    assert get_clip(client, clip) == clip


def test_aliases_canonicalize_edits_search_counts_and_do_not_chain(client, film):
    clip = film(["tackling", "other"])
    tag = term(client, "tackling")
    write(client, f"tags/{tag['id']}/aliases", {"name": "  Wrap   Up "}, status=201)
    response = client.patch(
        f"/api/clips/{clip['id']}",
        json={
            "version": clip["version"],
            "tags": ["WRAP UP", "tackling", "Other"],
        },
    )
    assert response.status_code == 200
    assert response.json()["tags"] == ["other", "tackling"]
    for filters in ({"tag": " WRAP UP "}, {"q": "wrap up"}, {"q": "tackling"}):
        result = client.get("/api/clips", params=filters).json()
        assert result["total"] == 1 and len(result["items"]) == 1
    assert {t["name"]: t["count"] for t in client.get("/api/tags").json()} == {
        "other": 1,
        "tackling": 1,
    }
    write(client, "tags", {"name": "wrap up"}, status=409)
    other = term(client, "other")
    write(client, f"tags/{other['id']}/aliases", {"name": "wrap up"}, status=409)
    write(client, f"tags/{tag['id']}/aliases/remove", {"name": "wrap up"})
    assert client.get("/api/clips", params={"tag": "wrap up"}).json()["total"] == 0
    assert term(client, "tackling")["count"] == 1


def test_presets_are_editable_additive_idempotent_and_safe_to_delete(client, film):
    clip = film(["existing"])
    for name in ("first step", "pursuit"):
        write(client, "tags", {"name": name}, status=201)
    ids = [term(client, n)["id"] for n in ("first step", "pursuit")]
    write(client, "presets", {"name": "Linebacker study", "tag_ids": ids}, status=201)
    preset = snapshot(client)["presets"][0]
    write(
        client,
        f"presets/{preset['id']}",
        {
            "name": "LB study",
            "description": "Watch feet",
            "tag_ids": ids,
        },
        method="put",
    )
    route = f"/api/clips/{clip['id']}/presets/{preset['id']}/apply"
    body = {"version": clip["version"], "expected_revision": snapshot(client)["revision"]}
    response = client.post(route, json=body)
    assert response.status_code == 200, response.text
    updated = response.json()
    assert updated["tags"] == ["existing", "first step", "pursuit"]
    assert updated["phase"] == clip["phase"] and updated["player_ids"] == []
    assert client.post(route, json=body).status_code == 409
    body["version"] = updated["version"]
    assert client.post(route, json=body).json()["version"] == updated["version"]
    result = client.delete(
        f"/api/taxonomy/presets/{preset['id']}",
        params={
            "expected_revision": snapshot(client)["revision"],
        },
    )
    assert result.status_code == 200 and result.json()["presets"] == []
    assert get_clip(client, clip) == updated


def test_rename_preview_migrates_archive_presets_and_preserves_history(client, film):
    first, second = film(["old", "other"]), film(["old"], archived=True)
    old = term(client, "old")
    write(client, f"tags/{old['id']}/aliases", {"name": "legacy slang"}, status=201)
    write(client, "presets", {"name": "Study", "tag_ids": [old["id"]]}, status=201)
    history_route = f"/api/clips/{first['id']}/history"
    history = client.get(history_route).json()
    plan = preview(client, "old", " New Label ")
    assert plan["operation"] == "rename"
    assert {c["id"] for c in plan["affected_clips"]} == {first["id"], second["id"]}
    assert len(plan["affected_presets"]) == 1
    assert plan["active_count_after"] == plan["archived_count_after"] == 1
    assert get_clip(client, first) == first  # preview is read-only
    result = apply(client, plan)
    assert result["taxonomy"]["presets"][0]["tag_ids"] == [old["id"]]
    assert term(client, "new label")["aliases"] == [
        {"name": "legacy slang", "protected": False},
        {"name": "old", "protected": True},
    ]
    for clip in (first, second):
        updated = get_clip(client, clip)
        assert "new label" in updated["tags"] and "old" not in updated["tags"]
        assert updated["version"] == clip["version"] + 1
        assert updated["source_sha256"] == clip["source_sha256"]
    assert client.get(history_route).json()[1:] == history
    restored = client.post(
        f"/api/clips/{first['id']}/restore",
        json={
            "version": first["version"],
            "current_version": first["version"] + 1,
        },
    )
    assert restored.status_code == 200
    assert restored.json()["tags"] == ["new label", "other"]
    write(client, f"tags/{old['id']}/aliases/remove", {"name": "old"}, status=409)
    assert client.get("/api/clips", params={"tag": "old"}).json()["total"] == 1
    assert (
        client.get("/api/clips", params={"q": "legacy slang", "archived": True}).json()["total"]
        == 1
    )


def test_mapping_unfamiliar_name_deduplicates_clips_and_presets(client, film):
    first = film(["unknown jargon", "tackling"])
    second = film(["unknown jargon"])
    third = film(["tackling"], archived=True)
    source, target = term(client, "unknown jargon"), term(client, "tackling")
    write(client, "presets", {"name": "Mixed", "tag_ids": [source["id"], target["id"]]}, status=201)
    write(client, f"tags/{source['id']}/aliases", {"name": "old alternate"}, status=201)
    write(client, f"tags/{target['id']}/aliases", {"name": "target alternate"}, status=201)
    plan = preview(client, source["name"], "target alternate")
    assert plan["operation"] == "map" and plan["target"]["name"] == "tackling"
    result = apply(client, plan)["taxonomy"]
    assert len(result["tags"]) == 1
    assert result["presets"][0]["tag_ids"] == [target["id"]]
    assert term(client, "tackling")["count"] == 2
    assert term(client, "tackling")["archived_count"] == 1
    for clip in (first, second, third):
        assert get_clip(client, clip)["tags"] == ["tackling"]
    for tag in ("unknown jargon", "old alternate", "target alternate"):
        assert client.get("/api/clips", params={"tag": tag}).json()["total"] == 2
    event = client.get("/api/taxonomy/events").json()[0]
    assert event["kind"] == "term_mapped"
    assert event["payload"]["source"]["id"] == source["id"]


@pytest.mark.parametrize("conflict", ["clip_edit", "new_source_use", "target_use", "taxonomy_edit"])
def test_preview_rejects_stale_population_and_metadata(client, film, conflict):
    clip = film(["old"])
    film(["target"])
    plan = preview(client, "old", "target")
    if conflict == "clip_edit":
        client.patch(
            f"/api/clips/{clip['id']}", json={"version": clip["version"], "title": "Updated"}
        )
    elif conflict == "new_source_use":
        film(["old"])
    elif conflict == "target_use":
        film(["target"], archived=True)
    else:
        write(client, "groups", {"name": "New group"}, status=201)
    before = snapshot(client)
    assert apply(client, plan, status=409)["error"]["code"] == "taxonomy_preview_stale"
    assert snapshot(client) == before


def test_injected_rename_failure_rolls_back_every_table(client, film, monkeypatch):
    film(["old"])
    film(["old", "target"])
    source, target = term(client, "old"), term(client, "target")
    write(client, "presets", {"name": "Study", "tag_ids": [source["id"], target["id"]]}, status=201)
    plan = preview(client, "old", "target")
    catalog = client.app.state.catalog
    with catalog.db.connection() as connection:
        before = "\n".join(connection.iterdump())
    original, calls = catalog._patch, []

    def failing(connection, clip_id, patch):
        calls.append(clip_id)
        result = original(connection, clip_id, patch)
        if len(calls) == 2:
            raise sqlite3.OperationalError("injected write failure")
        return result

    monkeypatch.setattr(catalog, "_patch", failing)
    assert apply(client, plan, status=503)["error"]["code"] == "taxonomy_storage_error"
    with catalog.db.connection() as connection:
        assert "\n".join(connection.iterdump()) == before
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_alias_promotion_and_repeat_rename_keep_search_names(client, film):
    clip = film(["a"])
    tag_id = term(client, "a")["id"]
    write(client, f"tags/{tag_id}/aliases", {"name": "b"}, status=201)
    for source, target in (("a", "b"), ("b", "c"), ("c", "a")):
        apply(client, preview(client, source, target))
    for name in ("a", "b", "c"):
        assert client.get("/api/clips", params={"tag": name}).json()["items"][0]["id"] == clip["id"]
    assert len(snapshot(client)["tags"]) == 1


@pytest.mark.parametrize(
    "body",
    [
        {"name": ""},
        {"name": "\x00"},
        {"name": "x" * 65},
        {"name": 9},
        {"name": "x", "expected_revision": True},
        {"name": "x", "extra": "ignored?"},
        {"name": "x", "group_id": str(uuid4())},
    ],
)
def test_invalid_term_inputs_do_not_change_catalog(client, body):
    before = snapshot(client)
    response = client.post(
        "/api/taxonomy/tags", json={"expected_revision": before["revision"], **body}
    )
    assert response.status_code in (404, 422)
    assert snapshot(client) == before


def test_conflicts_duplicate_names_and_invalid_presets(client, film):
    film(["a"])
    before = snapshot(client)
    write(client, "groups", {"name": "new"}, status=201)
    response = client.post(
        "/api/taxonomy/groups", json={"expected_revision": before["revision"], "name": "stale"}
    )
    assert response.status_code == 409
    for ids in ([], [str(uuid4())], [term(client, "a")["id"]] * 2):
        response = client.post(
            "/api/taxonomy/presets",
            json={
                "name": "invalid",
                "expected_revision": snapshot(client)["revision"],
                "tag_ids": ids,
            },
        )
        assert response.status_code in (404, 422)
    assert snapshot(client)["presets"] == []
    write(client, "groups", {"name": "NEW"}, status=409)


def test_preset_tag_limit_is_atomic(client, film):
    clip = film([f"tag-{i}" for i in range(40)])
    write(client, "tags", {"name": "extra"}, status=201)
    write(
        client,
        "presets",
        {"name": "Too many", "tag_ids": [term(client, "extra")["id"]]},
        status=201,
    )
    state = snapshot(client)
    response = client.post(
        f"/api/clips/{clip['id']}/presets/{state['presets'][0]['id']}/apply",
        json={
            "version": clip["version"],
            "expected_revision": state["revision"],
        },
    )
    assert response.status_code == 422 and response.json()["error"]["code"] == "preset_tag_limit"
    assert get_clip(client, clip) == clip


def legacy_catalog(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.executescript(SCHEMA)
        connection.execute(
            "INSERT INTO assets VALUES('a','hash','sources/x.mp4','x.mp4',1,10,1,1,25,0,'then')"
        )
        connection.execute(
            "INSERT INTO clips(id,asset_id,title,start_seconds,end_seconds,created_at,updated_at) "
            "VALUES('c','a','Legacy',0,10,'then','then')"
        )
        connection.execute("INSERT INTO clip_tags VALUES('c','unfamiliar/é')")
        connection.execute(
            "INSERT INTO revisions VALUES('c',1,?, 'then')", ('{"tags":["unfamiliar/é"]}',)
        )


def test_v1_migration_preserves_history_and_consistent_backup(tmp_path):
    path = tmp_path / "catalog.sqlite3"
    legacy_catalog(path)
    database = Database(path)
    backups = list(tmp_path.glob("catalog.pre-taxonomy-v2.*.sqlite3"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as backup:
        assert backup.execute("PRAGMA user_version").fetchone()[0] == 1
        assert backup.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert backup.execute("SELECT tag FROM clip_tags").fetchone()[0] == "unfamiliar/é"
    with database.connection() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
        assert connection.execute("SELECT tag FROM clip_tags").fetchone()[0] == "unfamiliar/é"
        assert (
            connection.execute("SELECT snapshot FROM revisions").fetchone()[0]
            == '{"tags":["unfamiliar/é"]}'
        )
        assert connection.execute("SELECT group_id FROM tag_terms").fetchone()[0] is None
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        group = connection.execute("SELECT id FROM tag_groups LIMIT 1").fetchone()[0]
    with database.connection(write=True) as connection:
        connection.execute("DELETE FROM tag_groups WHERE id=?", (group,))
    Database(path)
    with database.connection() as connection:
        assert connection.execute("SELECT count(*) FROM tag_groups").fetchone()[0] == 5
    assert len(list(tmp_path.glob("catalog.pre-taxonomy-v2.*.sqlite3"))) == 1


def test_migration_failure_rolls_back_all_ddl_and_data(tmp_path, monkeypatch):
    import exais_football.db as db_module

    path = tmp_path / "catalog.sqlite3"
    legacy_catalog(path)
    original = db_module.migrate_v2

    def fail(connection):
        original(connection)
        raise sqlite3.OperationalError("migration failure")

    monkeypatch.setattr(db_module, "migrate_v2", fail)
    with pytest.raises(sqlite3.OperationalError, match="migration failure"):
        Database(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
        assert (
            connection.execute("SELECT name FROM sqlite_master WHERE name='tag_terms'").fetchone()
            is None
        )
        assert connection.execute("SELECT tag FROM clip_tags").fetchone()[0] == "unfamiliar/é"
    monkeypatch.setattr(db_module, "migrate_v2", original)
    Database(path)


def test_backup_failure_and_concurrent_startup(tmp_path, monkeypatch):
    path = tmp_path / "catalog.sqlite3"
    legacy_catalog(path)
    original = Database._backup_v1

    def fail(self):
        raise OSError("disk full")

    monkeypatch.setattr(Database, "_backup_v1", fail)
    with pytest.raises(OSError, match="disk full"):
        Database(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
    monkeypatch.setattr(Database, "_backup_v1", original)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: Database(path), range(2)))
    assert len(list(tmp_path.glob("catalog.pre-taxonomy-v2.*.sqlite3"))) == 1


def test_real_video_and_frozen_job_survive_rename_and_reopen(client, import_clip):
    clip = import_clip(client)
    catalog = client.app.state.catalog
    clip = catalog.patch_clip(clip["id"], ClipPatch(version=clip["version"], tags=["old"]))
    job = client.app.state.jobs.enqueue(clip["id"])
    before_asset = catalog.asset(clip["id"])
    with catalog.db.connection() as connection:
        frozen = connection.execute(
            "SELECT snapshot FROM jobs WHERE id=?", (job["id"],)
        ).fetchone()[0]
    apply(client, preview(client, "old", "new"))
    assert file_sha256(catalog.source_path(clip["id"])) == clip["source_sha256"]
    assert catalog.asset(clip["id"]) == before_asset
    with catalog.db.connection() as connection:
        assert (
            connection.execute("SELECT snapshot FROM jobs WHERE id=?", (job["id"],)).fetchone()[0]
            == frozen
        )
    reopened = Catalog(catalog.settings)
    assert reopened.get_clip(clip["id"])["tags"] == ["new"]
    assert json.loads(frozen)["tags"] == ["old"]


def test_unicode_alias_literal_search_and_html_names(client, film):
    film(["straße", "<script>alert(1)</script>"])
    tag = term(client, "strasse")
    write(client, f"tags/{tag['id']}/aliases", {"name": "100%_Man"}, status=201)
    assert client.get("/api/clips", params={"tag": "STRASSE"}).json()["total"] == 1
    assert client.get("/api/clips", params={"q": "100%_"}).json()["total"] == 1
    assert client.get("/api/clips", params={"q": "100zz"}).json()["total"] == 0


def test_fresh_start_is_optional_and_private(client):
    state = snapshot(client)
    assert len(state["groups"]) == 6 and state["tags"] == state["presets"] == []
    assert state["revision"] == 1 and client.get("/api/taxonomy/events").json() == []


def test_concurrent_fresh_start_never_reinitializes_v2(tmp_path):
    path = tmp_path / "catalog.sqlite3"
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: Database(path), range(2)))
    with Database(path).connection() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
        assert connection.execute("SELECT count(*) FROM tag_groups").fetchone()[0] == 6
    assert not list(tmp_path.glob("catalog.pre-taxonomy-v2.*.sqlite3"))


@pytest.mark.parametrize("failure_code", [sqlite3.SQLITE_BUSY, sqlite3.SQLITE_IOERR])
def test_wal_setup_retries_only_busy_and_restores_timeout(monkeypatch, failure_code):
    import exais_football.db as db_module

    class Connection:
        def __init__(self):
            self.statements = []
            self.attempts = 0

        def execute(self, statement):
            self.statements.append(statement)
            if statement == "PRAGMA journal_mode=WAL":
                self.attempts += 1
                if self.attempts == 1:
                    exc = sqlite3.OperationalError("injected journal failure")
                    exc.sqlite_errorcode = failure_code
                    raise exc
            self.value = 10000 if statement == "PRAGMA busy_timeout" else "wal"
            return self

        def fetchone(self):
            return (self.value,)

    connection = Connection()
    sleeps = []
    monkeypatch.setattr(db_module.time, "sleep", sleeps.append)
    if failure_code == sqlite3.SQLITE_BUSY:
        Database._enable_wal(connection)
        assert connection.attempts == 2 and len(sleeps) == 1
    else:
        with pytest.raises(sqlite3.OperationalError, match="injected journal failure"):
            Database._enable_wal(connection)
        assert connection.attempts == 1 and sleeps == []
    assert connection.statements[-1] == "PRAGMA busy_timeout=10000"


def test_wal_setup_stops_at_deadline_and_preserves_database(tmp_path, monkeypatch):
    import exais_football.db as db_module

    path = tmp_path / "busy.sqlite3"
    connection = sqlite3.connect(path, isolation_level=None)
    blocker = sqlite3.connect(path, isolation_level=None)
    try:
        connection.execute("CREATE TABLE original(value TEXT)")
        connection.execute("INSERT INTO original VALUES ('preserved')")
        connection.execute("PRAGMA busy_timeout=4321")
        blocker.execute("BEGIN")
        blocker.execute("SELECT * FROM original").fetchall()
        ticks = iter((0.0, 11.0))
        monkeypatch.setattr(db_module.time, "monotonic", lambda: next(ticks))
        with pytest.raises(sqlite3.OperationalError) as error:
            Database._enable_wal(connection)
        assert error.value.sqlite_errorcode & 0xFF == sqlite3.SQLITE_BUSY
        assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == 4321
        assert connection.execute("SELECT value FROM original").fetchone()[0] == "preserved"
        assert not connection.in_transaction
    finally:
        blocker.close()
        connection.close()
