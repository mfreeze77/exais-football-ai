"""Explicit, previewed Kinovea imports and external comparison handoff."""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter
from pydantic import Field

from .kva_review import KVAReview
from .models import Contract


class KVAPreview(Contract):
    source_confirmed: bool


class KVAApply(KVAPreview):
    preview_token: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    decision: Literal["accepted", "rejected"]
    choices: dict[str, Literal["catalog", "kva"]]


def router(catalog):
    routes = APIRouter(prefix="/api/study")
    service = KVAReview(catalog)

    @routes.post("/clips/{clip_id}/kva/preview")
    def preview(clip_id: UUID, body: KVAPreview):
        return service.preview(str(clip_id), body.source_confirmed)

    @routes.post("/clips/{clip_id}/kva/apply")
    def apply(clip_id: UUID, body: KVAApply):
        return service.apply(str(clip_id), body)

    @routes.get("/clips/{clip_id}/kva/history")
    def history(clip_id: UUID):
        return service.history(str(clip_id))

    return routes
