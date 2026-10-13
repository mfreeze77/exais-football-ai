"""Same-origin terminology endpoints. No filesystem paths or executable inputs."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query

from .taxonomy import Taxonomy
from .taxonomy_models import (
    AliasInput,
    ChangeApply,
    ChangePreview,
    NamedItem,
    PresetApply,
    PresetInput,
    TermCreate,
    TermDetails,
)


def router(catalog) -> APIRouter:
    api = APIRouter(prefix="/api")
    taxonomy = Taxonomy(catalog)

    @api.get("/taxonomy")
    def get_taxonomy():
        return taxonomy.snapshot()

    @api.get("/taxonomy/events")
    def events(limit: Annotated[int, Query(ge=1, le=100)] = 30):
        return taxonomy.events(limit)

    @api.post("/taxonomy/groups", status_code=201)
    def create_group(body: NamedItem):
        return taxonomy.save_group(body)

    @api.put("/taxonomy/groups/{group_id}")
    def update_group(group_id: UUID, body: NamedItem):
        return taxonomy.save_group(body, str(group_id))

    @api.delete("/taxonomy/groups/{group_id}")
    def delete_group(group_id: UUID, expected_revision: Annotated[int, Query(ge=1)]):
        return taxonomy.delete_group(str(group_id), expected_revision)

    @api.post("/taxonomy/tags", status_code=201)
    def create_tag(body: TermCreate):
        return taxonomy.save_term(body)

    @api.put("/taxonomy/tags/{tag_id}")
    def update_tag(tag_id: UUID, body: TermDetails):
        return taxonomy.save_term(body, str(tag_id))

    @api.post("/taxonomy/tags/{tag_id}/aliases", status_code=201)
    def add_alias(tag_id: UUID, body: AliasInput):
        return taxonomy.alias(str(tag_id), body)

    @api.post("/taxonomy/tags/{tag_id}/aliases/remove")
    def remove_alias(tag_id: UUID, body: AliasInput):
        return taxonomy.alias(str(tag_id), body, remove=True)

    @api.post("/taxonomy/presets", status_code=201)
    def create_preset(body: PresetInput):
        return taxonomy.save_preset(body)

    @api.put("/taxonomy/presets/{preset_id}")
    def update_preset(preset_id: UUID, body: PresetInput):
        return taxonomy.save_preset(body, str(preset_id))

    @api.delete("/taxonomy/presets/{preset_id}")
    def delete_preset(preset_id: UUID, expected_revision: Annotated[int, Query(ge=1)]):
        return taxonomy.delete_preset(str(preset_id), expected_revision)

    @api.post("/taxonomy/changes/preview")
    def preview(body: ChangePreview):
        return taxonomy.preview(body)

    @api.post("/taxonomy/changes/apply")
    def apply(body: ChangeApply):
        return taxonomy.apply_change(body)

    @api.post("/clips/{clip_id}/presets/{preset_id}/apply")
    def apply_preset(clip_id: UUID, preset_id: UUID, body: PresetApply):
        return taxonomy.apply_preset(str(clip_id), str(preset_id), body)

    return api
