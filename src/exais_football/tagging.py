"""Shared tag namespace and additive schema migration; no media/model side effects."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from uuid import uuid4

DEFAULT_GROUPS = ("Phase", "Skill", "Formation", "Play concept", "Result", "Review purpose")
DDL = (
    """CREATE TABLE taxonomy_state (
        id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL CHECK(revision>=1))""",
    "INSERT INTO taxonomy_state VALUES(1,1)",
    """CREATE TABLE tag_groups (
        id TEXT PRIMARY KEY, name TEXT NOT NULL, name_key TEXT NOT NULL UNIQUE,
        description TEXT NOT NULL DEFAULT '')""",
    """CREATE TABLE tag_terms (
        id TEXT PRIMARY KEY, group_id TEXT REFERENCES tag_groups(id) ON DELETE SET NULL,
        description TEXT NOT NULL DEFAULT '')""",
    """CREATE TABLE tag_names (
        name TEXT PRIMARY KEY COLLATE NOCASE,
        tag_id TEXT NOT NULL REFERENCES tag_terms(id),
        is_canonical INTEGER NOT NULL CHECK(is_canonical IN (0,1)),
        is_legacy INTEGER NOT NULL DEFAULT 0 CHECK(is_legacy IN (0,1)))""",
    "CREATE UNIQUE INDEX tag_one_canonical ON tag_names(tag_id) WHERE is_canonical=1",
    "CREATE INDEX tag_names_term ON tag_names(tag_id)",
    """CREATE TABLE tag_presets (
        id TEXT PRIMARY KEY, name TEXT NOT NULL, name_key TEXT NOT NULL UNIQUE,
        description TEXT NOT NULL DEFAULT '')""",
    """CREATE TABLE tag_preset_items (
        preset_id TEXT NOT NULL REFERENCES tag_presets(id) ON DELETE CASCADE,
        tag_id TEXT NOT NULL REFERENCES tag_terms(id), PRIMARY KEY(preset_id,tag_id))""",
    """CREATE TABLE taxonomy_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT, revision INTEGER NOT NULL,
        kind TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL)""",
)


def normalize_tag(value: str) -> str:
    """Keep the original ClipPatch normalization, including Unicode case folding."""
    name = " ".join(value.casefold().split())
    if not name or len(name) > 64 or "\x00" in name:
        raise ValueError("Tags must contain 1 to 64 characters")
    return name


def revision(connection: sqlite3.Connection) -> int:
    return connection.execute("SELECT revision FROM taxonomy_state WHERE id=1").fetchone()[0]


def changed(connection: sqlite3.Connection, kind: str, payload: dict) -> int:
    connection.execute("UPDATE taxonomy_state SET revision=revision+1 WHERE id=1")
    value = revision(connection)
    connection.execute(
        "INSERT INTO taxonomy_events(revision,kind,payload,created_at) VALUES(?,?,?,?)",
        (
            value,
            kind,
            json.dumps(payload, ensure_ascii=False, allow_nan=False, sort_keys=True),
            datetime.now(UTC).isoformat(timespec="microseconds"),
        ),
    )
    return value


def resolve(connection: sqlite3.Connection, name: str) -> sqlite3.Row | None:
    return connection.execute(
        """SELECT t.id,n.name,t.group_id,t.description FROM tag_names a
        JOIN tag_terms t ON t.id=a.tag_id
        JOIN tag_names n ON n.tag_id=t.id AND n.is_canonical=1 WHERE a.name=?""",
        (name,),
    ).fetchone()


def canonical_tags(connection: sqlite3.Connection, names: list[str]) -> list[str]:
    """Resolve known aliases and retain new free text, within the caller's transaction."""
    canonical, created = set(), []
    for name in sorted({normalize_tag(value) for value in names}):
        term = resolve(connection, name)
        if term is None:
            term_id = str(uuid4())
            connection.execute("INSERT INTO tag_terms(id) VALUES(?)", (term_id,))
            connection.execute(
                "INSERT INTO tag_names(name,tag_id,is_canonical) VALUES(?,?,1)", (name, term_id)
            )
            created.append({"id": term_id, "name": name})
        canonical.add(term["name"] if term else name)
    if created:
        changed(connection, "free_text_registered", {"terms": created})
    return sorted(canonical)


def migrate_v2(connection: sqlite3.Connection) -> None:
    """Caller owns BEGIN IMMEDIATE and backup; exceptions roll back all DDL/data."""
    for statement in DDL:
        connection.execute(statement)
    for name in DEFAULT_GROUPS:
        connection.execute(
            "INSERT INTO tag_groups(id,name,name_key) VALUES(?,?,?)",
            (str(uuid4()), name, name.casefold()),
        )
    # Register, do not relabel. Existing strings/history stay byte-for-byte unchanged.
    for row in connection.execute("SELECT DISTINCT tag FROM clip_tags ORDER BY tag").fetchall():
        term_id = str(uuid4())
        connection.execute("INSERT INTO tag_terms(id) VALUES(?)", (term_id,))
        connection.execute(
            "INSERT INTO tag_names(name,tag_id,is_canonical) VALUES(?,?,1)", (row[0], term_id)
        )
    connection.execute("PRAGMA user_version=2")
