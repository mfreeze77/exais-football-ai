"""Validated film-study requests; no arbitrary SQL, commands, or filesystem paths."""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from .models import Contract, Phase, ReviewStatus, Title
from .tagging import normalize_tag

Version = Annotated[int, Field(strict=True, ge=1)]
Time = Annotated[float, Field(ge=0)]


class SelectedClip(Contract):
    clip_id: UUID
    version: Version


class TagEdit(Contract):
    mode: Literal["add", "remove", "replace"]
    values: Annotated[list[str], Field(max_length=40)]

    @field_validator("values")
    @classmethod
    def normalize(cls, values):
        return sorted({normalize_tag(v) for v in values})


class BatchChanges(Contract):
    game_id: UUID | None = None
    player_ids: Annotated[list[UUID], Field(max_length=40)] | None = None
    tags: TagEdit | None = None
    review_status: ReviewStatus | None = None
    phase: Phase | None = None
    favorite: bool | None = None
    archived: bool | None = None

    @model_validator(mode="after")
    def check(self):
        if not self.model_fields_set:
            raise ValueError("Select at least one batch action")
        for field in self.model_fields_set - {"game_id"}:
            if getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        if self.player_ids is not None and len(self.player_ids) != len(set(self.player_ids)):
            raise ValueError("Player IDs must be unique")
        return self


class BatchRequest(Contract):
    clips: Annotated[list[SelectedClip], Field(min_length=1, max_length=500)]
    changes: BatchChanges

    @field_validator("clips")
    @classmethod
    def unique(cls, clips):
        if len({v.clip_id for v in clips}) != len(clips):
            raise ValueError("Select each clip once")
        return clips


class BatchApply(BatchRequest):
    preview_token: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


class ViewCreate(Contract):
    version: Version
    title: Title
    start_seconds: Time
    end_seconds: Annotated[float, Field(gt=0)]
    inherit_labels: bool = False


class ClipFilters(Contract):
    q: Annotated[str, Field(max_length=200)] | None = None
    game_id: UUID | None = None
    player_id: UUID | None = None
    tag: Annotated[str, Field(max_length=64)] | None = None
    phase: Phase | None = None
    review_status: ReviewStatus | None = None
    favorite: bool | None = None
    archived: bool = False


class SavedSearchCreate(Contract):
    title: Title
    filter_version: Literal[1] = 1
    filters: ClipFilters


class SavedSearchPatch(SavedSearchCreate):
    version: Version


class SessionCreate(Contract):
    playlist_id: UUID


class SessionProgress(Contract):
    version: Version
    index: Annotated[int, Field(strict=True, ge=0)]
    at_seconds: Time
    action: Literal["position", "replay", "watch_again", "clear_watch_again", "finish", "reset"] = (
        "position"
    )


class ComparisonCreate(Contract):
    title: Title
    left_clip_id: UUID
    right_clip_id: UUID
    left_anchor: Time
    right_anchor: Time
    left_zoom: Annotated[float, Field(ge=1, le=4)] = 1
    right_zoom: Annotated[float, Field(ge=1, le=4)] = 1


class ComparisonPatch(ComparisonCreate):
    version: Version


class NormalizedPoint(Contract):
    x: Annotated[float, Field(ge=0, le=1)]
    y: Annotated[float, Field(ge=0, le=1)]


class Drawing(Contract):
    id: UUID
    kind: Literal["arrow", "circle", "text", "freehand"]
    points: Annotated[list[NormalizedPoint], Field(min_length=1, max_length=1000)]
    start_seconds: Time
    end_seconds: Annotated[float, Field(gt=0)]
    text: Annotated[str, Field(max_length=300)] = ""
    color: Literal["yellow", "white", "red", "blue", "green"] = "yellow"
    width: Annotated[float, Field(ge=0.001, le=0.03)] = 0.004

    @model_validator(mode="after")
    def shape(self):
        if self.end_seconds <= self.start_seconds:
            raise ValueError("Drawing interval must have positive duration")
        if self.kind in {"arrow", "circle"} and len(self.points) != 2:
            raise ValueError("Arrow and circle need two points")
        if self.kind == "text" and (len(self.points) != 1 or not self.text):
            raise ValueError("Text needs one point and a label")
        if self.kind == "freehand" and len(self.points) < 2:
            raise ValueError("Freehand needs at least two points")
        return self


class DrawingSave(Contract):
    version: Annotated[int, Field(strict=True, ge=0)]
    source_sha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    drawings: Annotated[list[Drawing], Field(max_length=100)]

    @field_validator("drawings")
    @classmethod
    def unique(cls, drawings):
        if len({d.id for d in drawings}) != len(drawings):
            raise ValueError("Drawing IDs must be unique")
        return drawings


class DrawingRestore(Contract):
    version: Annotated[int, Field(strict=True, ge=0)]
    restore_revision: Version


class SnapshotCreate(Contract):
    source_sha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    drawing_revision: Annotated[int, Field(strict=True, ge=0)]
    at_seconds: Time
    png_base64: Annotated[str, Field(min_length=24, max_length=12_000_000)]
