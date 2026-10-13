"""Same-origin routes for film-study workflows."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query
from fastapi import Path as APIPath
from pydantic import Field

from .frames import frame_image, frame_index, timeline_preview
from .models import Contract, EmptyRequest
from .storage import LeasedFileResponse
from .study import Study
from .study_models import (
    BatchApply,
    BatchRequest,
    ComparisonCreate,
    ComparisonPatch,
    DrawingRestore,
    DrawingSave,
    SavedSearchCreate,
    SavedSearchPatch,
    SessionCreate,
    SessionProgress,
    SnapshotCreate,
    ViewCreate,
)
from .workspace import workspace_lock


class PreviewAccept(Contract):
    preview_token: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


def router(catalog) -> APIRouter:
    api = APIRouter(prefix="/api/study")
    study = Study(catalog)

    @api.post("/batches/preview")
    def batch_preview(body: BatchRequest):
        return study.preview_batch(body)

    @api.post("/batches", status_code=201)
    def batch_apply(body: BatchApply):
        return study.apply_batch(body, body.preview_token)

    @api.get("/batches")
    def batches():
        return study.batches()

    @api.post("/batches/{batch_id}/undo")
    def batch_undo(batch_id: UUID, body: EmptyRequest):
        return study.undo_batch(str(batch_id))

    @api.post("/clips/{clip_id}/views", status_code=201)
    def view_create(clip_id: UUID, body: ViewCreate):
        return study.create_view(str(clip_id), body)

    @api.get("/clips/{clip_id}/views")
    def views(clip_id: UUID):
        return study.views(str(clip_id))

    @api.get("/searches")
    def searches():
        return study.searches()

    @api.post("/searches", status_code=201)
    def search_create(body: SavedSearchCreate):
        return study.save_search(body)

    @api.put("/searches/{search_id}")
    def search_update(search_id: UUID, body: SavedSearchPatch):
        return study.save_search(body, str(search_id))

    @api.delete("/searches/{search_id}")
    def search_delete(search_id: UUID, version: Annotated[int, Query(ge=1)]):
        return study.delete_search(str(search_id), version)

    @api.get("/searches/{search_id}/results")
    def search_results(
        search_id: UUID,
        limit: Annotated[int, Query(ge=1, le=100)] = 60,
        offset: Annotated[int, Query(ge=0)] = 0,
    ):
        return study.search_results(str(search_id), limit, offset)

    @api.post("/searches/{search_id}/snapshot-preview")
    def snapshot_preview(search_id: UUID, body: EmptyRequest):
        return study.search_snapshot(str(search_id))

    @api.post("/searches/{search_id}/playlist", status_code=201)
    def snapshot_playlist(search_id: UUID, body: PreviewAccept):
        return study.search_snapshot(str(search_id), body.preview_token)

    @api.get("/comparisons")
    def comparisons():
        return study.comparisons()

    @api.get("/comparisons/{comparison_id}")
    def comparison(comparison_id: UUID):
        return study.comparison(str(comparison_id))

    @api.post("/comparisons", status_code=201)
    def comparison_create(body: ComparisonCreate):
        return study.save_comparison(body)

    @api.put("/comparisons/{comparison_id}")
    def comparison_update(comparison_id: UUID, body: ComparisonPatch):
        return study.save_comparison(body, str(comparison_id))

    @api.post("/comparisons/{comparison_id}/kinovea/launch")
    def native_comparison(comparison_id: UUID, body: EmptyRequest):
        from .native_comparison import launch_comparison

        return launch_comparison(catalog, str(comparison_id))

    @api.get("/sessions")
    def sessions():
        return study.sessions()

    @api.post("/sessions", status_code=201)
    def session_create(body: SessionCreate):
        return study.create_session(str(body.playlist_id))

    @api.get("/sessions/{session_id}")
    def session(session_id: UUID):
        return study.session(str(session_id))

    @api.patch("/sessions/{session_id}")
    def session_update(session_id: UUID, body: SessionProgress):
        return study.update_session(str(session_id), body)

    @api.get("/clips/{clip_id}/drawings")
    def drawings(clip_id: UUID, revision: Annotated[int | None, Query(ge=1)] = None):
        return study.drawings(str(clip_id), revision)

    @api.put("/clips/{clip_id}/drawings")
    def drawings_save(clip_id: UUID, body: DrawingSave):
        return study.save_drawings(str(clip_id), body)

    @api.post("/clips/{clip_id}/drawings/restore")
    def drawings_restore(clip_id: UUID, body: DrawingRestore):
        return study.restore_drawings(str(clip_id), body.version, body.restore_revision)

    @api.post("/clips/{clip_id}/snapshots", status_code=201)
    def snapshot(clip_id: UUID, body: SnapshotCreate):
        return study.save_snapshot(str(clip_id), body)

    @api.get("/clips/{clip_id}/timeline")
    def timeline(clip_id: UUID, count: Annotated[int, Query(ge=2, le=12)] = 9):
        return timeline_preview(catalog, str(clip_id), count)

    @api.get("/clips/{clip_id}/frame-index")
    def frame_index_read(clip_id: UUID):
        return frame_index(catalog, str(clip_id))

    @api.post("/clips/{clip_id}/frame-index")
    def frame_index_create(clip_id: UUID, body: EmptyRequest):
        return frame_index(catalog, str(clip_id), create=True)

    @api.get("/clips/{clip_id}/frames/{frame_number}")
    def frame(clip_id: UUID, frame_number: Annotated[int, APIPath(ge=0)]):
        with workspace_lock(catalog):
            return LeasedFileResponse(
                frame_image(catalog, str(clip_id), frame_number), media_type="image/png"
            )

    return api
