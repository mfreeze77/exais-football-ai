# Backlog implementation and delivery status

## Published source, not a completed release

PR #50 (EFA-017) was merged at `89da49bbe6388d630f1f1134fbe4df57983af32a`. PR #51 contains the recovered implementation source and targets main. Publication is no longer blocked on a local patch.

The exact 439745-byte candidate patch has SHA-256 `fba5be6505cb8ac748a3c556092dfa5b0ef9e35bbba497e636b93ea55734b287`. [Recovery run 38476746395](https://github.com/mfreeze77/exais-football-ai/actions/runs/38476746395) verified that checksum, applied the patch, passed locked Python tests/lint/format/build and published `07699bd202b620566903bfc5dd7441f3bfed61af`. A downloaded source comparison confirmed all 69 source and test files were byte-identical to the recovered candidate. Temporary transport and the one-time publishing workflow are removed. Normal CI has read-only repository permissions.

## Executed qualification

[Verification run 38477281938](https://github.com/mfreeze77/exais-football-ai/actions/runs/38477281938) passed on source commit `56194399af6eff5cc9fd2cb807128bffcc1c5e81`:

- Both Python 3.11 and 3.12 jobs, including the 222-test suite, lint, formatting, ticket validation, source/wheel builds and isolated installed-app checks.
- The original Firefox film-room/taxonomy workflow.
- The additional `scripts/backlog_ui.mjs` workflow: bulk preview/apply/undo, coaching-note preservation, source-sharing cuts, comparison anchors, exact decoded frames, saved drawings, timeline entries, no implicit model execution, saved collections, frozen sessions/watch-again, verified backup and responsive dialog geometry.

The latest documentation commit changes no runtime or test source. Its own final-head Actions run remains the merge gate. Screenshots were inspected for comparison controls, drawing controls and small-screen recovery. This is not a complete accessibility audit or representative-film evaluation. Timeline entry creation is tested; the screenshot does not prove every lazy-loaded image was decoded at capture time.

The suite retains the 124-test baseline and adds study, recovery, CV, KVA and native/CLI contract tests. Native/CLI fixtures are not a running native Kinovea application. Model integration tests do not establish real-model accuracy. An existing Starlette test-client deprecation warning is not suppressed.

## Implementation included

- Previewed, version-checked all-or-nothing bulk edits and recoverable undo; independent cuts sharing immutable original media.
- Decoded-frame indexes, timeline previews, source-relative drawing revisions and annotated stills, saved comparisons, dynamic collections and frozen resumable sessions.
- Versioned KVA import plans, explicit reconciliation and external comparison handoff.
- Explicit CV environment configuration, calibration/pre-snap checks, persistent jobs, source-bound artifacts, separate correction/identity decisions, audio-preserving overlays and evaluation utilities.
- Full-library backup/verification, create-only staged restore, portable packages, integrity checks, protected derived-file cleanup and local launch commands.

Open **Film-study tools** in the sidebar. Select one clip for drawing/calibration/KVA, two for comparisons, or multiple for bulk edits. Preview never writes; applying a bulk preview is an atomic transaction. Undo refuses incompatible later edits. First evaluate an upgraded copy rather than your only film library.

## Open backlog acceptance

This delivery publishes implemented work; it does not declare all EFA-018 through EFA-048 accepted. Existing ticket checkboxes are unchanged pending per-ticket acceptance reconciliation. Do not read an uncompleted historical ticket as proof that no code exists, or treat this source delivery as proof every checkbox has passed.

Remaining work includes native Windows/Kinovea round trips and supported-version qualification; actual pinned CV inference and owner-approved film evaluation; calibration-transfer/tracking-quality enhancements; desktop installer builds and signing/notarization; large-library performance; complete accessibility/media-matrix evaluation; and final release acceptance. These are not silently replaced with synthetic tests.

## Recovery and integration safety

Original media is not rewritten. Source-relative timing and hashes bind drawings and analysis. Human roster identities remain separate from anonymous model track IDs. Starting the app does not acquire model weights or call an external model service; configuration and analysis are explicit owner actions.

Full backup covers required film and catalog content. The earlier schema-2 migration backup was database-only. Restore first previews archive identity, then requires `--execute --expected-sha256` into a new destination; an existing destination is refused. Use `exais-football restore --help` and `exais-football cv-configure --help` for the explicit CLI contracts.

The same-origin API schema is served at `/api/openapi.json`; interactive `/docs` and `/redoc` pages are disabled. New routes are defined in `study_routes.py`, `kva_routes.py`, `cv_routes.py` and `work_routes.py`; validation and error cases are exercised in the corresponding study, KVA, CV, recovery and native/CLI test modules.
