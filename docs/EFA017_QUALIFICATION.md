# EFA-017 qualification and acceptance evidence

Scope: [EFA-017](tickets/EFA-017.md), delivered in [PR #50](https://github.com/mfreeze77/exais-football-ai/pull/50) after merged PR #49. The implementation is a complete API, persistence, migration, and browser workflow, not only a schema or scaffold. No merge is performed by this work.

## Executed qualification

The complete implementation at **d99824fe11e90e383a83c47e70d6bb7645f4554d** passed [GitHub Actions run 38347274235](https://github.com/mfreeze77/exais-football-ai/actions/runs/38347274235):

| Gate | Observed result |
| --- | --- |
| Locked Python 3.11 and 3.12 suites | **124 passed** in each environment: original 94 plus 30 taxonomy/migration/concurrency cases. |
| Ruff lint and formatting | Passed, including all 26 Python files and qualification scripts. |
| Roadmap schema and dependency validation | 48 tickets, four milestones, 255 acceptance checks, 48 issue mappings; acyclic dependencies. |
| Source distribution and wheel | Built and checked successfully. |
| Separate installed-wheel application | Started outside the source directory; health and all original/new JS/CSS assets served correctly. |
| Tag-input JavaScript tests | Quoted/comma/Unicode round trips and malformed-input rejection passed. |
| Real Firefox workflow | Original eight film-room groups and four EFA-017 groups passed using Playwright 1.62.1 and generated media. |
| Browser evidence | Seven synthetic screenshots retained as the `browser-qualification-screenshots` Actions artifact; no user film. |

The Python 3.12 job is [115131476662](https://github.com/mfreeze77/exais-football-ai/actions/runs/38347274235/job/115131476662); the Python 3.11 job is [115131476469](https://github.com/mfreeze77/exais-football-ai/actions/runs/38347274235/job/115131476469). Later PR checks qualify the final source revision, including the screenshot-driven checkbox-layout refinement and its desktop/small-screen geometry assertion.

Local checks separately passed the earlier 121-test candidate and the final 30-test taxonomy suite, plus Ruff and JavaScript checks. The local Python environment differs from the lock; final full-suite and browser qualification comes from the recorded GitHub Actions environments, not an unrun local-browser claim. The existing Starlette/httpx test-client deprecation warning remains visible; it is not suppressed.

## All five acceptance checks

| Ticket requirement | Implemented behavior and executed coverage |
| --- | --- |
| Optional useful preset | Named 1–40-term presets; review additions preserve existing labels, metadata and note drafts. API checks cover additive application, no-op repeats, limits and stale versions. Firefox creates/edits/applies a preset and saves the review. |
| One canonical identity per alias | Shared unique namespace, no alias chains, canonicalization on edit/restore, alias-aware filters/search without duplicate rows or inflated counts. Python tests cover collisions, Unicode and literal wildcard characters; Firefox checks original names and aliases after renaming. |
| Previewed atomic rename | Read-only affected-clip/preset preview, revision-bound digest, recheck under a write transaction, new clip revisions and audit history. Four stale-plan cases and injected mid-update failure verify rejection and full rollback. Firefox edits a clip from another tab before applying and confirms conflict/draft preservation, then successful re-preview. |
| Preset deletion retains applied tags | Preset membership is independent of clip labels. API and Firefox verify deleting the shortcut leaves the clip record unchanged. |
| Unfamiliar labels preserved and mappable | Migration registers existing labels as ungrouped terms without rewriting clip strings or history. Explicit mapping into a known term deduplicates clip/preset entries and preserves old names as protected aliases. Python and Firefox exercise unknown-term mapping. |

## Integrity and migration

Original video hashes, source-relative times, notes, immutable analysis payloads, old revision snapshots and queued export intent remain unchanged by terminology management. Renames create new metadata revisions; restoring older snapshots resolves current canonical names rather than resurrecting duplicates.

Schema-1 migration first creates a verified SQLite **database-only** backup. It includes committed WAL state, uses an exclusive new filename, and refuses to upgrade if backup creation fails. DDL, legacy-term registration and schema-version advancement commit together. Failure injection confirms rollback; concurrent old/fresh startup cannot reset an initialized catalog.

Remote qualification exposed a journal-mode startup race. The fix retries only `SQLITE_BUSY` before beginning a transaction, with a ten-second deadline and short individual waits. Non-busy storage errors fail immediately, and the connection's previous timeout is restored on every exit. Tests exercise transient contention, a real held read lock/deadline, and an injected I/O error.

## Boundaries

All six starter groups are optional and editable; none applies labels automatically. A Phase-group tag does not change the separate structured Phase field. No model runs, automatic tactical labels, source deletion, provider uploads, or roster-identity inference are added.

The migration backup is not a complete film-library backup and does not close EFA-042 or the broader EFA-044 recovery work. Native Windows/Kinovea and actual football-cv qualification remain separate. The feature uses the existing dependency lock and preserves the existing license.

Owner instructions, API requests, error codes, concurrency behavior and recovery guidance are in [TAG_TAXONOMY.md](TAG_TAXONOMY.md).
