# EFA-017 — Football terminology, aliases, and presets

This additive schema-2 feature extends the original film-room workflow. It does not
run models, impose a playbook, rewrite original footage, or equate tags with roster
identity. The six initial groups are **Phase, Skill, Formation, Play concept,
Result, and Review purpose**. They are empty, optional, and editable. No tags or
presets are automatically applied to film.

## Owner workflow

Open **Tags & presets** in the sidebar. **Groups** creates, renames, describes, and
deletes groups. Deleting a group moves its terms to **Ungrouped**; it does not
remove tags from clips. **Tags** includes every saved free-text tag, including
unfamiliar legacy vocabulary, with active and archived usage counts. Assign a
term to a group or leave it ungrouped. Descriptions are optional. Filtering this
list matches names, aliases, and descriptions.

A tag's **Add alias** action registers an alternative spelling. Names share one
namespace: an existing canonical tag or alias cannot be silently reassigned.
When two existing terms mean the same thing, use **Rename or map**, not Add alias.
Ordinary aliases can be removed. Old canonical names retained by a rename/map are
protected so historical snapshots and old searches can still resolve correctly.

**Presets** stores a named set of 1–40 existing terms. Only a name and at least one
tag are required. Edit or delete the shortcut independently of the clips on which
it has been used. Deleting a preset never removes applied labels or tag definitions.

In clip review, choose a group and an available tag, or select a saved preset.
**Add tag to draft** and **Add preset to draft** preserve existing labels, metadata
edits, and note drafts. They do not immediately write to the catalog. **Save
changes** persists the review normally. Applying phase-group tags does not change
the separate structured **Phase** field. Aliases resolve to canonical names on
save, and the form shows the saved names without replacing text entered during an
in-flight save.

Free text remains supported. Commas separate tags; quote a tag containing a comma
and double a literal quote inside a quoted tag. For example:

```text
first step, "cut, ""block"""
```

This means two tags, `first step` and `cut, "block"`. Existing tags containing
commas or quotes now round-trip through the browser editor without being split.
Malformed quoted input blocks saving while retaining the draft.

## Rename and explicit mapping

**Rename or map** first produces a read-only preview. A new name renames the
source term while preserving its stable ID. A name or alias belonging to another
term maps the source into that existing term. The preview lists affected clips,
current versions, archive state, before/after labels, affected presets, and the
resulting active/archived counts. A map also explains that the target's group and
description are kept; the audit record preserves the source definition.

**Apply reviewed change** executes one write transaction. It checks the preview
against the current terminology revision and source/target clip populations,
including clip versions. A changed clip, new use of either term, changed preset,
new free-text term, or intervening terminology edit makes the preview stale.
The request then returns `409 taxonomy_preview_stale` without applying anything.
The user can preview again; the typed target name is retained.

An accepted change updates clip-tag associations, canonical names/aliases, preset
memberships, usage counts, and the audit record together. Each affected clip gets
a new metadata revision. If both names were on a clip or preset, the result has
one canonical entry, not duplicates. A simulated write failure after a clip update
is covered by rollback tests. No partial clip/preset/name state is committed.

Original media, stored analysis payloads, notes, prior revision snapshots, and
frozen export jobs remain unchanged. Restoring an old clip revision creates a new
revision using current canonical names. It does not resurrect a retired spelling
as a second canonical term. A map is not automatically reversible: previous
snapshots and the terminology audit retain what happened, but separating merged
meanings again requires a deliberate new editing operation. Full disaster
recovery and general migration tooling remain EFA-042/EFA-044.

## HTTP API

All routes use the existing same-origin/loopback protections and JSON request
limits. IDs are UUIDs. All named-resource updates use **replacement semantics**
for editable fields; send both description and group/membership values to keep
them. Omitting an optional description clears it; omitting a group's assignment
on a tag makes that tag ungrouped. This differs from the existing clip PATCH API.

| Method and route (under `/api`) | Input / result |
| --- | --- |
| `GET /taxonomy` | `revision`, `groups`, `tags`, `presets`; terms include canonical name, aliases with `protected`, group, description, active `count`, and `archived_count`. Presets contain stable `tag_ids`. |
| `POST /taxonomy/groups` | `{expected_revision, name, description?}` → updated snapshot; 201. |
| `PUT /taxonomy/groups/{id}` | Same replacement fields → snapshot. |
| `DELETE /taxonomy/groups/{id}?expected_revision=N` | Remove group only; terms become ungrouped → snapshot. |
| `POST /taxonomy/tags` | `{expected_revision, name, group_id?, description?}` → snapshot; 201. |
| `PUT /taxonomy/tags/{id}` | `{expected_revision, group_id?, description?}` → snapshot. Name changes require preview/apply. |
| `POST /taxonomy/tags/{id}/aliases` | `{expected_revision, name}` → snapshot; 201. |
| `POST /taxonomy/tags/{id}/aliases/remove` | Same body → snapshot. Canonical/protected names cannot be removed. |
| `POST /taxonomy/presets` | `{expected_revision, name, description?, tag_ids}` → snapshot; 201. |
| `PUT /taxonomy/presets/{id}` | Same replacement fields → snapshot. |
| `DELETE /taxonomy/presets/{id}?expected_revision=N` | Delete shortcut/membership only → snapshot. |
| `POST /taxonomy/changes/preview` | `{tag_id, new_name}` → plan with source/target definitions, affected records, counts, and `preview_token`. No writes. |
| `POST /taxonomy/changes/apply` | `{tag_id, new_name, preview_token}` → `{taxonomy, result}`. Recomputes the plan under a write lock. |
| `POST /clips/{clip_id}/presets/{preset_id}/apply` | `{version, expected_revision}` → updated clip; adds tags atomically. Reapplying an already-effective preset does not add a clip revision. This is an immediate API operation, unlike the browser's draft button. |
| `GET /taxonomy/events?limit=30` | Newest audit entries first, maximum 100; `{id, revision, kind, payload, created_at}`. |

Tags keep the original whitespace-collapse and Unicode-casefold normalization,
with 1–64 normalized characters. Group/preset names use 1–80 characters and a
casefolded uniqueness key; descriptions allow up to 2,000 characters. Presets
require 1–40 unique known IDs. A clip still has a maximum of 40 tags. Unknown
fields, invalid UUIDs, invalid/non-finite values, and boolean revision numbers are
rejected by strict request models. Names containing HTML are data, never markup.

`GET /tags` retains its original `{name,count}` response shape for active clips.
The existing `GET /clips?tag=...` resolves aliases inside the read transaction.
Free-text `q` searches include aliases via `EXISTS`, so matching several names
cannot duplicate clip rows or inflate totals. Search `%`, `_`, and backslashes
remain literal escaped input.

### Errors

Responses retain the application's `{error:{code,message}}` envelope.

| Code | Status | Meaning / action |
| --- | --- | --- |
| `taxonomy_conflict` | 409 | Terminology revision changed; retain the draft and reload/reconcile. |
| `taxonomy_preview_stale` | 409 | The reviewed plan changed; obtain a new preview. |
| `taxonomy_name_conflict` | 409 | Duplicate group/preset name, occupied term name, or an alias collision; use an explicit previewed map for existing tags. |
| `protected_tag_name` | 409 | Cannot remove the canonical or historical search name. |
| `taxonomy_not_found` | 404 | The referenced group, term, preset, or removable alias does not exist. |
| `unchanged_tag_name` | 422 | Requested canonical name is unchanged. |
| `preset_tag_limit` | 422 | Additive preset would exceed 40 tags; no clip changes saved. |
| `taxonomy_storage_error` | 503 | SQLite write/commit failed; the transaction rolled back. Check disk space and retry. |
| `version_conflict` | 409 | Existing clip optimistic-concurrency check failed during preset application. |
| `validation_error` | 422 | Existing strict request-validation envelope. |

The browser does not claim success on uncertain transport failures. It retains
the draft and asks the owner to reload to confirm the server state before retry.
A deterministic preview digest is a stale-state check, **not an authorization or
CSRF token**; the application retains its local single-owner trust boundary.

## Persistence and migration

New tables are `taxonomy_state`, `tag_groups`, `tag_terms`, `tag_names`,
`tag_presets`, `tag_preset_items`, and `taxonomy_events`. A shared unique name
namespace prevents chains/cycles and canonical/alias collisions. Preset membership
uses stable term IDs. Existing `clip_tags` stores canonical strings, preserving
the old response contracts and source-relative revision model.

Schema-1 startup first creates a consistent **database-only** backup using
SQLite's backup API, named `catalog.pre-taxonomy-v2.<uuid>.sqlite3` beside the live
catalog. The upgrade registers legacy tags as ungrouped definitions without
altering their strings, clip versions, or saved snapshots. DDL/data and
`PRAGMA user_version=2` commit together under `BEGIN IMMEDIATE`. Startup rechecks
the version after acquiring the writer lock, including fresh catalogs, so two
initializers cannot reset a migrated database. Backups are verified and closed
before migration continues. A backup failure prevents migration; a migration
failure rolls back the schema and data. Newer unsupported schemas are rejected.
Reopening schema 2 does not create more backups or recreate deleted default groups.

SQLite's documented transaction and backup behavior informs this implementation:
[transactions](https://www.sqlite.org/lang_transaction.html),
[online backup](https://www.sqlite.org/backup.html), and
[Python connection backup](https://docs.python.org/3/library/sqlite3.html#sqlite3.Connection.backup).

The backup is **not** a complete media-library backup. It does not contain video
files or replace EFA-042. Do not delete the original recordings or overwrite a
running catalog to attempt a downgrade. Stop the app before any manual recovery,
retain the current catalog and all media, and restore a copied pre-migration
catalog into a separate workspace for verification first.

## Qualification entry points

- `python -m pytest -q tests/test_taxonomy.py`: migration, concurrency, aliases,
  read-only/stale previews, mapping/rename rollback, history, preset lifecycle,
  input limits, real source hashes, and frozen export snapshots.
- `node scripts/tag_input_test.mjs`: exact quoted-tag round trips and malformed input.
- `node scripts/ui_smoke.mjs`: original film-room workflow followed by the
  EFA-017 browser journey in `scripts/taxonomy_ui.mjs`.
- `scripts/check_wheel.py` and `scripts/check_installed.py`: the new JS/CSS assets
  are checked in the actual installed package, not only the source checkout.

Execution results and tested revisions belong in `docs/tickets/EFA-017.md` and
the PR. Defining a test is not a claim that it ran. Native Windows/Kinovea and
actual computer-vision execution remain separately unqualified by this ticket.
