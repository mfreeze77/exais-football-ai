"""Additive film-study and recovery schema. Never rewrite historical snapshots."""

from __future__ import annotations

import sqlite3

V3 = (
    "\nCREATE TABLE batch_edits (\n id TEXT PRIMARY KEY, payload TEXT "
    "NOT NULL, undone INTEGER NOT NULL DEFAULT 0,\n created_at TEXT "
    "NOT NULL, undone_at TEXT\n);\nCREATE TABLE view_origins (\n clip_id "
    "TEXT PRIMARY KEY REFERENCES clips(id), parent_clip_id TEXT NOT "
    "NULL REFERENCES clips(id),\n source_sha256 TEXT NOT NULL, "
    "created_at TEXT NOT NULL\n);\nCREATE TABLE saved_searches (\n id "
    "TEXT PRIMARY KEY, title TEXT NOT NULL, filter_version INTEGER "
    "NOT NULL,\n filters TEXT NOT NULL, version INTEGER NOT NULL "
    "DEFAULT 1, created_at TEXT NOT NULL\n);\nCREATE TABLE "
    "study_sessions (\n id TEXT PRIMARY KEY, playlist_id TEXT NOT NULL "
    "REFERENCES playlists(id), snapshot TEXT NOT NULL,\n progress TEXT "
    "NOT NULL, version INTEGER NOT NULL DEFAULT 1, created_at TEXT "
    "NOT NULL,\n updated_at TEXT NOT NULL\n);\nCREATE TABLE comparisons "
    "(\n id TEXT PRIMARY KEY, title TEXT NOT NULL, left_clip_id TEXT "
    "NOT NULL REFERENCES clips(id),\n right_clip_id TEXT NOT NULL "
    "REFERENCES clips(id), payload TEXT NOT NULL,\n version INTEGER "
    "NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT "
    "NOT NULL\n);\nCREATE TABLE drawing_revisions (\n clip_id TEXT NOT "
    "NULL REFERENCES clips(id), revision INTEGER NOT NULL, payload "
    "TEXT NOT NULL,\n created_at TEXT NOT NULL, PRIMARY "
    "KEY(clip_id,revision)\n);\nCREATE TABLE artifact_records (\n id "
    "TEXT PRIMARY KEY, kind TEXT NOT NULL, clip_id TEXT REFERENCES "
    "clips(id),\n path TEXT NOT NULL UNIQUE, sha256 TEXT NOT NULL, "
    "manifest TEXT NOT NULL,\n created_at TEXT NOT NULL, stale INTEGER "
    "NOT NULL DEFAULT 0\n);\nCREATE TABLE kva_imports (\n id TEXT "
    "PRIMARY KEY, clip_id TEXT NOT NULL REFERENCES clips(id), "
    "sidecar_sha256 TEXT NOT NULL,\n payload TEXT NOT NULL, status "
    "TEXT NOT NULL CHECK(status IN ('accepted','rejected')),\n "
    "created_at TEXT NOT NULL\n);\nCREATE TABLE calibrations (\n id TEXT "
    "PRIMARY KEY, clip_id TEXT NOT NULL REFERENCES clips(id), "
    "source_sha256 TEXT NOT NULL,\n revision INTEGER NOT NULL, payload "
    "TEXT NOT NULL, created_at TEXT NOT NULL,\n "
    "UNIQUE(clip_id,revision)\n);\nCREATE TABLE calibration_transfers "
    "(\n id TEXT PRIMARY KEY, target_clip_id TEXT NOT NULL REFERENCES "
    "clips(id), payload TEXT NOT NULL,\n status TEXT NOT NULL "
    "CHECK(status IN "
    "('needs_review','accepted','rejected','failed')),\n created_at "
    "TEXT NOT NULL\n);\nCREATE TABLE work_jobs (\n id TEXT PRIMARY KEY, "
    "kind TEXT NOT NULL, clip_id TEXT REFERENCES clips(id),\n status "
    "TEXT NOT NULL CHECK(status IN "
    "('queued','running','complete','failed','cancelled','interrupted'"
    ")),\n snapshot TEXT NOT NULL, result TEXT, error TEXT, stage TEXT "
    "NOT NULL DEFAULT 'queued',\n attempt INTEGER NOT NULL DEFAULT 1, "
    "parent_id TEXT REFERENCES work_jobs(id),\n created_at TEXT NOT "
    "NULL, updated_at TEXT NOT NULL\n);\nCREATE TABLE cv_runs (\n id "
    "TEXT PRIMARY KEY, clip_id TEXT NOT NULL REFERENCES clips(id), "
    "source_sha256 TEXT NOT NULL,\n manifest TEXT NOT NULL, directory "
    "TEXT NOT NULL, review_version INTEGER NOT NULL DEFAULT 0,\n "
    "continuity_version INTEGER NOT NULL DEFAULT 0, created_at TEXT "
    "NOT NULL\n);\nCREATE TABLE cv_decisions (\n id TEXT PRIMARY KEY, "
    "run_id TEXT NOT NULL REFERENCES cv_runs(id), track_id TEXT NOT "
    "NULL,\n kind TEXT NOT NULL CHECK(kind IN "
    "('position','identity','continuity')),\n payload TEXT NOT NULL, "
    "revision INTEGER NOT NULL, continuity_version INTEGER NOT NULL,\n "
    "created_at TEXT NOT NULL\n);\nCREATE INDEX cv_decisions_run ON "
    "cv_decisions(run_id,kind,revision);\nCREATE INDEX cv_runs_clip ON "
    "cv_runs(clip_id,created_at);\nCREATE INDEX jobs_work_status ON "
    "work_jobs(status,created_at);\nCREATE INDEX artifact_clip ON "
    "artifact_records(clip_id,kind,created_at);\nCREATE INDEX "
    "views_asset ON clips(asset_id);\nPRAGMA user_version=3;\n"
)


def migrate_v3(connection: sqlite3.Connection) -> None:
    if connection.execute("PRAGMA user_version").fetchone()[0] != 2:
        raise RuntimeError("Film-study migration requires schema 2")
    for statement in V3.split(";"):
        if statement.strip():
            connection.execute(statement)
