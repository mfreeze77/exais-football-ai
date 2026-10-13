"""Explicit source-bound calibration, inference intent, and human correction contracts."""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, model_validator

from .models import Contract

Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Revision = Annotated[int, Field(strict=True, ge=0)]
Position = Literal["DL", "LB", "CB", "S", "OL", "QB", "RB", "WR", "unknown", "suppressed"]


class CalibrationPoint(Contract):
    px: float
    py: float
    fx: Annotated[float, Field(ge=0, le=100)]
    fy: Annotated[float, Field(ge=0, le=53.334)]


class CalibrationRequest(Contract):
    source_sha256: Digest
    expected_revision: Revision
    frame: Annotated[int, Field(strict=True, ge=0, le=99999)]
    points: Annotated[list[CalibrationPoint], Field(min_length=4, max_length=64)]


class CalibrationSave(CalibrationRequest):
    preview_token: Digest


class Preflight(Contract):
    source_sha256: Digest
    calibration_id: UUID | None = None
    snap_frame: Annotated[int, Field(strict=True, ge=0, le=99999)] | None = None
    context_start: Annotated[float, Field(ge=0)] = 0
    context_end: Annotated[float, Field(gt=0)] | None = None
    camera_cuts: Annotated[list[float], Field(max_length=100)] = []
    context_reviewed: bool = False
    offense_direction: Literal["left", "right", "unknown"] = "unknown"
    offense_color: Annotated[str, Field(max_length=80)] = ""

    @model_validator(mode="after")
    def limits(self):
        if any(t < 0 for t in self.camera_cuts):
            raise ValueError("Cut timestamps must be positive")
        return self


class InferenceRequest(Preflight):
    environment_fingerprint: Digest


class PositionDecision(Contract):
    expected_revision: Revision
    start_seconds: Annotated[float, Field(ge=0)]
    end_seconds: Annotated[float, Field(gt=0)]
    value: Position
    evidence: Annotated[str, Field(min_length=1, max_length=1000)]


class IdentityDecision(Contract):
    expected_revision: Revision
    start_seconds: Annotated[float, Field(ge=0)]
    end_seconds: Annotated[float, Field(gt=0)]
    player_id: UUID | None
    evidence: Annotated[str, Field(min_length=1, max_length=1000)]


class ContinuityDecision(Contract):
    expected_revision: Revision
    action: Literal["suppress", "restore", "split", "link", "unlink"]
    at_frame: Annotated[int, Field(strict=True, ge=0)] | None = None
    other_track_id: Annotated[str, Field(min_length=1, max_length=80)] | None = None
    evidence: Annotated[str, Field(min_length=1, max_length=1000)]


class TransferPair(Contract):
    reference_x: float
    reference_y: float
    target_x: float
    target_y: float


class TransferRequest(Contract):
    source_calibration_id: UUID
    target_source_sha256: Digest
    target_frame: Annotated[int, Field(strict=True, ge=0)]
    target_expected_revision: Revision
    pairs: Annotated[list[TransferPair], Field(min_length=4, max_length=64)]


class TransferApply(Contract):
    preview_token: Digest
    decision: Literal["accepted", "rejected"]


class OverlayRequest(Contract):
    expected_review_revision: Revision
    include_unreviewed: bool = False
    include_identities: bool = False
    start_seconds: Annotated[float, Field(ge=0)] = 0
    end_seconds: Annotated[float, Field(gt=0)] | None = None
