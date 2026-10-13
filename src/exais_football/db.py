"""SQLite catalog, transactional writes, and an explicit schema version."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

SCHEMA_VERSION = 1
SCHEMA = """
BEGIN IMMEDIATE;
CREATE TABLE IF NOT EXISTS games (
    id TEXT PRIMARY KEY, title TEXT NOT NULL, opponent TEXT NOT NULL,
    date TEXT NOT NULL DEFAULT '', season TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS players (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, jersey TEXT NOT NULL DEFAULT '',
    position TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS assets (
    id TEXT PRIMARY KEY, sha256 TEXT NOT NULL UNIQUE, path TEXT NOT NULL UNIQUE,
    filename TEXT NOT NULL, size INTEGER NOT NULL CHECK(size > 0),
    duration REAL NOT NULL CHECK(duration > 0), width INTEGER NOT NULL,
    height INTEGER NOT NULL, fps REAL NOT NULL, has_audio INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS clips (
    id TEXT PRIMARY KEY, asset_id TEXT NOT NULL REFERENCES assets(id),
    game_id TEXT REFERENCES games(id), title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
    phase TEXT NOT NULL DEFAULT 'other'
      CHECK(phase IN ('offense','defense','special_teams','other')),
    review_status TEXT NOT NULL DEFAULT 'new'
      CHECK(review_status IN ('new','review','approved','needs_work')),
    favorite INTEGER NOT NULL DEFAULT 0 CHECK(favorite IN (0,1)),
    archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN (0,1)),
    start_seconds REAL NOT NULL CHECK(start_seconds >= 0),
    end_seconds REAL NOT NULL CHECK(end_seconds > start_seconds),
    version INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS clip_tags (
    clip_id TEXT NOT NULL REFERENCES clips(id) ON DELETE CASCADE,
    tag TEXT NOT NULL COLLATE NOCASE, PRIMARY KEY(clip_id, tag)
);
CREATE TABLE IF NOT EXISTS clip_players (
    clip_id TEXT NOT NULL REFERENCES clips(id) ON DELETE CASCADE,
    player_id TEXT NOT NULL REFERENCES players(id), PRIMARY KEY(clip_id, player_id)
);
CREATE TABLE IF NOT EXISTS notes (
    id TEXT PRIMARY KEY, clip_id TEXT NOT NULL REFERENCES clips(id),
    body TEXT NOT NULL, at_seconds REAL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS revisions (
    clip_id TEXT NOT NULL REFERENCES clips(id), version INTEGER NOT NULL,
    snapshot TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(clip_id, version)
);
CREATE TABLE IF NOT EXISTS playlists (
    id TEXT PRIMARY KEY, title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS playlist_items (
    playlist_id TEXT NOT NULL REFERENCES playlists(id),
    clip_id TEXT NOT NULL REFERENCES clips(id), ordinal INTEGER NOT NULL,
    PRIMARY KEY(playlist_id, clip_id), UNIQUE(playlist_id, ordinal)
);
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY, clip_id TEXT NOT NULL REFERENCES clips(id),
    kind TEXT NOT NULL, status TEXT NOT NULL
      CHECK(status IN ('queued','running','complete','failed')),
    clip_version INTEGER NOT NULL, snapshot TEXT NOT NULL, output_path TEXT,
    error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS analyses (
    id TEXT PRIMARY KEY, clip_id TEXT NOT NULL REFERENCES clips(id),
    payload TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'unreviewed'
      CHECK(status IN ('unreviewed','accepted','rejected')),
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS analysis_reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT, analysis_id TEXT NOT NULL REFERENCES analyses(id),
    status TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sidecars (
    clip_id TEXT PRIMARY KEY REFERENCES clips(id), path TEXT NOT NULL,
    sha256 TEXT NOT NULL, summary TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS clips_game ON clips(game_id);
CREATE INDEX IF NOT EXISTS clips_review ON clips(archived,review_status,updated_at);
CREATE INDEX IF NOT EXISTS tags_lookup ON clip_tags(tag,clip_id);
CREATE INDEX IF NOT EXISTS player_lookup ON clip_players(player_id,clip_id);
CREATE INDEX IF NOT EXISTS notes_clip ON notes(clip_id,at_seconds);
CREATE INDEX IF NOT EXISTS jobs_state ON jobs(status,created_at);
PRAGMA user_version = 1;
COMMIT;
"""


class Database:
    def __init__(self, path: Path):
        self.path = path
        connection = self._open()
        try:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise RuntimeError("This catalog uses a newer schema; upgrade the application")
            connection.execute("PRAGMA journal_mode=WAL")
            if version == 0:
                connection.executescript(SCHEMA)
        finally:
            connection.close()

    def _open(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=10000")
        return connection

    @contextmanager
    def connection(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        connection = self._open()
        try:
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()
