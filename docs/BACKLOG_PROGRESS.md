# Backlog implementation and delivery status

## Published source, not a completed release

PR #50 (EFA-017) was merged at `89da49bbe6388d630f1f1134fbe4df57983af32a`.
PR #51 now contains the recovered implementation source at `07699bd202b620566903bfc5dd7441f3bfed61af`, rather than only a progress document. It targets `main`.

The source was restored from the exact 439745-byte local candidate patch with SHA-256 `fba5be6505cb8ac748a3c556092dfa5b0ef9e35bbba497e636b93ea55734b287`. [Recovery run 38476746395](https://github.com/mfreeze77/exais-football-ai/actions/runs/38476746395) verified the checksum, applied the patch, passed locked Python tests/lint/format/build and published source to the feature branch. Transport files were removed. The temporary publishing workflow is removed in the following verification commit; normal CI is read-only.

The candidate has 222 Python tests (124 retained baseline plus new study, recovery, CV, KVA, and native/CLI contract tests). Re-running the local suite during recovery passed all 222. This does not establish actual model accuracy or native application interoperability.

## Delivered implementation areas

- Previewed, version-checked all-or-nothing bulk edits and recoverable undo; independent cuts sharing immutable original media.
- Decoded frame indexes, timeline previews, source-relative drawings and still images, saved comparisons, dynamic collections and frozen resumable sessions.
- Versioned KVA import plans, explicit reconciliation and external comparison handoff.
- Optional configured CV environment, reviewed calibration/pre-snap inputs, persistent jobs, source-bound artifacts, correction/identity decisions, audio-preserving overlays and evaluation utilities.
- Full-library backup/verification, create-only staged restore, portable packages, integrity checks, derived-file cleanup and local launch commands.

Open **Film-study tools** in the sidebar. Select clips before using a tool: one for drawing/calibration/KVA, two for comparisons, multiple for bulk edits. Bulk preview never writes; applying the preview is an atomic transaction. Undo refuses incompatible later edits. Preserve the original workspace while first testing an upgraded copy.

## Gates that remain open

This delivery is not a declaration that EFA-018 through EFA-048 are all accepted. Their individual checks remain open until the code and executed evidence meet each complete ticket scope.

- Native Windows Kinovea round trips, supported-version compatibility and native comparison launch.
- Real pinned football-cv inference, hardware/platform behavior, representative owner-approved film and measured accuracy.
- Remaining calibration-transfer and tracking-quality enhancements.
- Desktop installer builds, signing/notarization, native macOS/Windows qualification.
- Large-library performance, complete accessibility/media-matrix evaluation and final release acceptance.

The new `scripts/backlog_ui.mjs` is an additional synthetic Firefox integration gate alongside the original film-room/taxonomy workflow. Consult the exact PR-head Actions run for its executed result; merely adding the script is not passing evidence. It checks bulk preview/apply/undo and note preservation, source-sharing cuts, comparison anchors, actual decoded frames, saved drawings, timeline previews, no implicit model execution, saved collections, frozen sessions, verified backup and responsive layout.

## Recovery and integration safety

Original media is not rewritten. Source-relative timing and source hashes bind analysis and drawings. Human identities are separate from anonymous model tracking IDs. Starting the app does not acquire model weights or call an external model service. The model environment must be explicitly configured by the owner.

Full backup covers required film and catalog content; the older schema-2 migration backup was database-only. Restore first previews archive identity, then requires `--execute --expected-sha256` into a new destination. Existing destinations are rejected. Use `exais-football restore --help` and `exais-football cv-configure --help` for the explicit command contracts.

The runtime API describes its new request models at `/docs` and `/openapi.json`. Route modules are `study_routes.py`, `kva_routes.py`, `cv_routes.py`, and `work_routes.py`. Schema, ownership and error cases are exercised in `tests/test_study.py`, `tests/test_kva_review.py`, `tests/test_cv_workflow.py`, `tests/test_recovery.py` and `tests/test_cli_native.py`.
