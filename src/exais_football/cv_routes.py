"""Opt-in local CV controls. No browser request supplies executables or arbitrary paths."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, File, Query, UploadFile
from starlette.concurrency import run_in_threadpool

from . import cv_environment
from .calibration import Calibration
from .cv_jobs import freeze_inference
from .cv_models import (
    CalibrationRequest,
    CalibrationSave,
    ContinuityDecision,
    IdentityDecision,
    InferenceRequest,
    OverlayRequest,
    PositionDecision,
    Preflight,
    TransferApply,
    TransferRequest,
)
from .cv_store import CVStore
from .models import EmptyRequest
from .overlay import freeze_overlay
from .service import CatalogError
from .storage import LeasedFileResponse


def router(catalog, jobs):
    routes = APIRouter(prefix="/api/cv")
    calibrations = Calibration(catalog)
    store = CVStore(catalog)

    @routes.get("/players/{player_id}/identities")
    def identities(
        player_id: UUID,
        limit: Annotated[int, Query(ge=1, le=100)] = 25,
        offset: Annotated[int, Query(ge=0)] = 0,
    ):
        from .identity_views import player_identities

        return player_identities(catalog, str(player_id), limit, offset)

    @routes.get("/environment")
    def environment():
        return cv_environment.capability(catalog)

    @routes.post("/environment/check")
    def check(body: EmptyRequest):
        return cv_environment.probe(catalog)

    @routes.get("/clips/{clip_id}/calibrations")
    def list_calibrations(clip_id: UUID):
        return calibrations.list(str(clip_id))

    @routes.post("/clips/{clip_id}/calibrations/preview")
    def preview(clip_id: UUID, body: CalibrationRequest):
        return calibrations.preview(str(clip_id), body)

    @routes.post("/clips/{clip_id}/calibrations", status_code=201)
    def save(clip_id: UUID, body: CalibrationSave):
        return calibrations.save(str(clip_id), body)

    @routes.post("/clips/{clip_id}/preflight")
    def preflight(clip_id: UUID, body: Preflight):
        return calibrations.preflight(str(clip_id), body)

    @routes.post("/clips/{clip_id}/analysis", status_code=202)
    def analyze(clip_id: UUID, body: InferenceRequest):
        return jobs.enqueue(
            "cv_analysis", freeze_inference(catalog, body, str(clip_id)), str(clip_id)
        )

    @routes.get("/clips/{clip_id}/runs")
    def runs(clip_id: UUID):
        return store.list(str(clip_id))

    @routes.post("/clips/{clip_id}/runs", status_code=201)
    async def import_bundle(clip_id: UUID, file: Annotated[UploadFile, File()]):
        temporary = catalog.settings.path(f"staging/{uuid4()}.analysis.zip")
        try:
            size = 0
            with temporary.open("xb") as output:
                while chunk := await file.read(1024**2):
                    size += len(chunk)
                    if size > 64 * 1024**2:
                        raise CatalogError("analysis_size", "Analysis package exceeds 64 MiB", 413)
                    await run_in_threadpool(output.write, chunk)
            return await run_in_threadpool(store.ingest, str(clip_id), temporary)
        finally:
            await file.close()
            temporary.unlink(missing_ok=True)

    @routes.get("/runs/{run_id}")
    def run(run_id: UUID):
        return {k: v for k, v in store.get(str(run_id)).items() if k != "directory"}

    @routes.get("/runs/{run_id}/projection")
    def projection(run_id: UUID):
        return store.projection(str(run_id))

    @routes.get("/runs/{run_id}/quality")
    def quality(run_id: UUID):
        return store.quality(str(run_id))

    @routes.get("/runs/{run_id}/decisions")
    def decisions(run_id: UUID):
        return store.decisions(str(run_id))

    @routes.get("/runs/{run_id}/files/{name}")
    def raw(run_id: UUID, name: str):
        return LeasedFileResponse(store.artifact(str(run_id), name), filename=name)

    @routes.post("/runs/{run_id}/tracks/{track_id}/position")
    def position(run_id: UUID, track_id: str, body: PositionDecision):
        return store.decide(str(run_id), track_id, "position", body)

    @routes.post("/runs/{run_id}/tracks/{track_id}/identity")
    def identity(run_id: UUID, track_id: str, body: IdentityDecision):
        return store.decide(str(run_id), track_id, "identity", body)

    @routes.post("/runs/{run_id}/tracks/{track_id}/continuity")
    def continuity(run_id: UUID, track_id: str, body: ContinuityDecision):
        return store.decide(str(run_id), track_id, "continuity", body)

    @routes.post("/runs/{run_id}/overlays", status_code=202)
    def overlay(run_id: UUID, body: OverlayRequest):
        snapshot = freeze_overlay(catalog, str(run_id), body)
        return jobs.enqueue("cv_overlay", snapshot, snapshot["clip"]["id"])

    @routes.post("/clips/{clip_id}/calibration-transfer", status_code=201)
    def transfer(clip_id: UUID, body: TransferRequest):
        return calibrations.transfer_preview(str(clip_id), body)

    @routes.post("/calibration-transfer/{transfer_id}")
    def accept_transfer(transfer_id: UUID, body: TransferApply):
        return calibrations.transfer_apply(str(transfer_id), body)

    return routes
