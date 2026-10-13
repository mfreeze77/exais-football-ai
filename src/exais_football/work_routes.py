"""Same-origin work/recovery endpoints. Browser requests never choose filesystem paths."""

from __future__ import annotations

import json
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter
from pydantic import Field

from .models import Contract, EmptyRequest
from .portable import freeze_selection
from .recovery import inspect_package, inspect_workspace
from .service import CatalogError, file_sha256
from .storage import LeasedFileResponse, artifacts, cleanup, preview_cleanup, report
from .study_models import SelectedClip
from .workspace import managed_path, workspace_lock


class PackageRequest(Contract):
    clips: Annotated[list[SelectedClip], Field(min_length=1, max_length=500)]
    include_media: bool = False
    include_sidecars: bool = True


class CleanupRequest(Contract):
    ids: Annotated[list[str], Field(min_length=1, max_length=1000)]


class CleanupApply(CleanupRequest):
    preview_token: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


def router(catalog, jobs):
    routes = APIRouter(prefix="/api")

    @routes.get("/work")
    def work_list():
        return jobs.list()

    @routes.get("/work/{job_id}")
    def work_get(job_id: UUID):
        return jobs.get(str(job_id))

    @routes.post("/work/{job_id}/cancel")
    def work_cancel(job_id: UUID, body: EmptyRequest):
        return jobs.cancel(str(job_id))

    @routes.post("/work/{job_id}/retry", status_code=202)
    def work_retry(job_id: UUID, body: EmptyRequest):
        return jobs.retry(str(job_id))

    @routes.post("/backups", status_code=202)
    def backup(body: EmptyRequest):
        return jobs.enqueue("backup", {"requested": "committed_catalog_and_required_files"})

    @routes.post("/packages", status_code=202)
    def package(body: PackageRequest):
        snapshot = freeze_selection(
            catalog,
            [c.model_dump(mode="json") for c in body.clips],
            body.include_media,
            body.include_sidecars,
        )
        return jobs.enqueue("package", snapshot)

    @routes.get("/integrity")
    def integrity():
        return inspect_workspace(catalog)

    @routes.post("/integrity", status_code=202)
    def verify_integrity(body: EmptyRequest):
        return jobs.enqueue("integrity", {})

    @routes.get("/storage")
    def storage_report():
        return report(catalog)

    @routes.post("/storage/preview")
    def storage_preview(body: CleanupRequest):
        return preview_cleanup(catalog, body.ids)

    @routes.post("/storage/cleanup")
    def storage_cleanup(body: CleanupApply):
        return cleanup(catalog, body.ids, body.preview_token)

    @routes.get("/artifacts")
    def list_artifacts(clip_id: UUID | None = None):
        return artifacts(catalog, str(clip_id) if clip_id else None)

    def artifact(record_id):
        with catalog.db.connection() as db:
            row = db.execute(
                "SELECT * FROM artifact_records WHERE id=?", (str(record_id),)
            ).fetchone()
            if row is None:
                raise CatalogError("artifact_not_found", "This artifact does not exist", 404)
            path = managed_path(catalog.settings.data_dir, row["path"])
            if file_sha256(path) != row["sha256"]:
                raise CatalogError("artifact_changed", "Artifact bytes failed verification", 409)
            return path, dict(row)

    @routes.get("/artifacts/{artifact_id}/file")
    def download(artifact_id: UUID):
        with workspace_lock(catalog):
            path, row = artifact(artifact_id)
            return LeasedFileResponse(path, filename=path.name)

    @routes.get("/artifacts/{artifact_id}/manifest")
    def manifest(artifact_id: UUID):
        path, row = artifact(artifact_id)
        return json.loads(row["manifest"])

    @routes.post("/artifacts/{artifact_id}/inspect")
    def inspect(artifact_id: UUID, body: EmptyRequest):
        path, row = artifact(artifact_id)
        if row["kind"] not in {"full_backup", "portable_package"}:
            raise CatalogError("not_package", "Select a backup or portable package", 422)
        return inspect_package(path)

    return routes
