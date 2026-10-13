"""Strict input contracts for owner-managed football terminology."""

from typing import Annotated
from uuid import UUID

from pydantic import Field, field_validator

from .models import Contract
from .tagging import normalize_tag

Revision = Annotated[int, Field(strict=True, ge=1)]
Name = Annotated[str, Field(min_length=1, max_length=80)]
Description = Annotated[str, Field(max_length=2000)]
TagName = Annotated[str, Field(min_length=1, max_length=256)]


class ExpectedRevision(Contract):
    expected_revision: Revision


class NamedItem(ExpectedRevision):
    name: Name
    description: Description = ""

    @field_validator("name")
    @classmethod
    def clean_name(cls, value):
        value = " ".join(value.split())
        if not value or "\x00" in value:
            raise ValueError("Choose a nonempty name without null characters")
        return value


class TermDetails(ExpectedRevision):
    group_id: UUID | None = None
    description: Description = ""


class TermCreate(TermDetails):
    name: TagName

    @field_validator("name")
    @classmethod
    def clean_tag(cls, value):
        return normalize_tag(value)


class AliasInput(ExpectedRevision):
    name: TagName

    @field_validator("name")
    @classmethod
    def clean_tag(cls, value):
        return normalize_tag(value)


class PresetInput(NamedItem):
    tag_ids: Annotated[list[UUID], Field(min_length=1, max_length=40)]

    @field_validator("tag_ids")
    @classmethod
    def unique_tags(cls, value):
        if len(set(value)) != len(value):
            raise ValueError("Preset tags must be unique")
        return value


class ChangePreview(Contract):
    tag_id: UUID
    new_name: TagName

    @field_validator("new_name")
    @classmethod
    def clean_tag(cls, value):
        return normalize_tag(value)


class ChangeApply(ChangePreview):
    preview_token: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


class PresetApply(ExpectedRevision):
    version: Revision
