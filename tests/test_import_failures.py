"""Originals remain recoverable when catalog failure overlaps another import."""

from __future__ import annotations

import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import pytest

from exais_football.service import file_sha256


def test_failed_import_cannot_delete_original_reused_after_rollback(
    client, sample_video, monkeypatch
):
    catalog = client.app.state.catalog
    first = catalog.settings.path("staging/first.upload")
    second = catalog.settings.path("staging/second.upload")
    shutil.copyfile(sample_video, first)
    shutil.copyfile(sample_video, second)
    expected_hash = file_sha256(sample_video)
    rolled_back, finish_failed_request = threading.Event(), threading.Event()
    real_connection = catalog.db.connection
    real_record_revision = catalog._record_revision

    class InjectedCatalogFailure(OSError):
        pass

    def fail_first_revision(connection, clip_id):
        if threading.current_thread().name.startswith("failed-import"):
            raise InjectedCatalogFailure("Catalog write failed after source publication")
        return real_record_revision(connection, clip_id)

    @contextmanager
    def pause_after_rollback(write=False):
        try:
            with real_connection(write=write) as connection:
                yield connection
        except InjectedCatalogFailure:
            # The DB write lock is already released. A second request is now free
            # to commit the same original before the first request unwinds.
            rolled_back.set()
            assert finish_failed_request.wait(timeout=15)
            raise

    monkeypatch.setattr(catalog, "_record_revision", fail_first_revision)
    monkeypatch.setattr(catalog.db, "connection", pause_after_rollback)
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="failed-import") as pool:
        failed = pool.submit(catalog.ingest, first, "first.mp4")
        try:
            assert rolled_back.wait(timeout=15)
            assert catalog.stats()["clips"] == 0
            successful = catalog.ingest(second, "second.mp4")
            assert successful["duplicate"] is False
            original = catalog.source_path(successful["clip"]["id"])
            assert file_sha256(original) == expected_hash
        finally:
            finish_failed_request.set()
        with pytest.raises(InjectedCatalogFailure):
            failed.result(timeout=15)

    assert original.is_file()
    assert file_sha256(original) == expected_hash
    assert catalog.stats()["clips"] == 1
    assert len(catalog.history(successful["clip"]["id"])) == 1
    assert list(catalog.settings.path("sources").rglob("*.mp4")) == [original]
