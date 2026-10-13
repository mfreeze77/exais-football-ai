"""Human metadata contracts. Model suggestions have separate contracts/storage."""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Phase = Literal["offense", "defense", "special_teams", "other"]
ReviewStatus = Literal["new", "review", "approved", "needs_work"]
Title = Annotated[str, Field(min_length=1, max_length=240)]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)


class GameCreate(Contract):
    title: Title
    opponent: Annotated[str, Field(min_length=1, max_length=160)]
    date: Annotated[str, Field(max_length=32)] = ""
    season: Annotated[str, Field(max_length=40)] = ""
    notes: Annotated[str, Field(max_length=10000)] = ""


class PlayerCreate(Contract):
    name: Annotated[str, Field(min_length=1, max_length=160)]
    jersey: Annotated[str, Field(max_length=12)] = ""
    position: Annotated[str, Field(max_length=64)] = ""


class ClipPatch(Contract):
    version: Annotated[int, Field(strict=True, ge=1)]
    title: Title | None = None
    game_id: UUID | None = None
    phase: Phase | None = None
    review_status: ReviewStatus | None = None
    favorite: bool | None = None
    archived: bool | None = None
    description: Annotated[str, Field(max_length=10000)] | None = None
    start_seconds: Annotated[float, Field(ge=0)] | None = None
    end_seconds: Annotated[float, Field(gt=0)] | None = None
    tags: Annotated[list[str], Field(max_length=40)] | None = None
    player_ids: Annotated[list[UUID], Field(max_length=40)] | None = None

    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, value):
        if value is None:
            return value
        normalized = sorted({" ".join(tag.casefold().split()) for tag in value})
        if any(not tag or len(tag) > 64 or "\x00" in tag for tag in normalized):
            raise ValueError("Tags must contain 1 to 64 characters")
        return normalized

    @model_validator(mode="after")
    def reject_null_updates(self):
        for name in self.model_fields_set - {"game_id"}:
            if getattr(self, name) is None:
                raise ValueError(f"{name} cannot be null")
        if self.player_ids is not None and len(set(self.player_ids)) != len(self.player_ids):
            raise ValueError("Player IDs must be unique")
        return self


class NoteCreate(Contract):
    body: Annotated[str, Field(min_length=1, max_length=10000)]
    at_seconds: Annotated[float, Field(ge=0)] | None = None


class RestoreRequest(Contract):
    version: Annotated[int, Field(strict=True, ge=1)]
    current_version: Annotated[int, Field(strict=True, ge=1)]


class PlaylistCreate(Contract):
    title: Title
    description: Annotated[str, Field(max_length=10000)] = ""


class PlaylistItems(Contract):
    clip_ids: Annotated[list[UUID], Field(max_length=1000)]

    @field_validator("clip_ids")
    @classmethod
    def unique_ids(cls, value):
        if len(set(value)) != len(value):
            raise ValueError("A clip can only appear once in a playlist")
        return value


class AnalysisReview(Contract):
    status: Literal["accepted", "rejected", "unreviewed"]


class EmptyRequest(Contract):
    pass
