# First-milestone qualification

Candidate: `build/film-library-foundation`, application version **0.1.0**. Local verification was executed on **2026-10-13**, using Python **3.12.14**, FFmpeg/ffprobe **6.1.1**, and the dependencies in `uv.lock`. The PR is the review boundary; tickets stay open until an accepted merge.

## Executed local checks

| Check | Observed result |
| --- | --- |
| `ruff check .` | Passed. |
| `ruff format --check src tests scripts docs/tickets/validate.py` | Passed; 21 Python files formatted. |
| `python docs/tickets/validate.py` | Passed: 48 tickets, four milestones, 255 acceptance checks, 48 real GitHub mappings, acyclic dependencies. |
| `pytest -q` | **94 passed** in 20.43 seconds. No skipped media gates. |
| JavaScript syntax | `node --check` passed for all four browser modules and `scripts/ui_smoke.mjs`. |
| Static HTML checks | 144 unique/known IDs, referenced static selectors present, no inline scripts or event handlers. |
| `python -m build` | Source distribution and wheel built successfully with isolated Hatchling 1.32.4. |
| `python scripts/check_wheel.py` | All required application/browser assets present; runtime media/catalog files absent. |
| Independent wheel installation | Installed into a separate virtual environment. `scripts/check_installed.py` started the installed app with a temporary catalog and retrieved health, HTML, CSS, and every JS module. Passed. |

The Python suite reports one dependency deprecation warning: Starlette's test client warns that its `httpx` path is deprecated in favor of `httpx2`. All assertions pass; this is test-tooling follow-up, not an application warning suppressed by the code.

## What the tests establish

### Original media and real exports

Tests generate video and audio in temporary directories. The media suite decodes a red/blue transition to establish that a selected non-keyframe cut starts at the intended content. It decodes an 880 Hz test tone and checks its amplitude and frequency. Silent clips, multiple audio streams, odd dimensions, and rotation metadata are also exercised.

Exports are H.264 MP4 with source audio streams mapped to AAC when present. Tests check duration tolerance, dimensions, codecs/audio count, full decode, and source/output SHA-256. A job captures its clip version and original-source interval before processing; changing the catalog selection afterward does not alter the queued export.

The renderer verifies source bytes before/after work, and the job verifies that the renderer's actual source hash agrees with the frozen catalog snapshot. A regression changes the source after the job precheck and performs a real export; the result is withheld with `source_changed` rather than published with false provenance.

### Persistent review and relationship integrity

Application tests exercise import and duplicate handling; versioned metadata and restore; combined search/filter queries; games and manually assigned roster IDs; timed notes; favorites/archive; ordered playlists; range/HEAD playback; jobs; safe KVA storage; and separate AI suggestion review.

Restart tests reopen the catalog after shutdown, preserving clips and notes. Previously interrupted work becomes failed with an explicit reason. An OS-level instance lock rejects a second running app that tries to own the same data directory. Two competing metadata writers cannot silently overwrite each other.

### Failure and publication behavior

Negative cases cover invalid/corrupt/empty/oversized media, unsafe filenames, traversal, missing relationships, stale edits, non-finite or out-of-bounds times, unsafe XML, unknown model schema/labels, and wrong-source analysis.

A regression reproduces concurrent import rollback after source publication. Published, content-addressed originals are retained when catalog insertion fails, so another successful import cannot lose a reused source. This may leave a verified orphan file for later recovery; automatic orphan cleanup is not part of M1.

Native-review tests use real filesystem copies and collision behavior while stubbing Windows capability and process launch. Video/KVA copies are staged, hash-checked, and published without overwriting a competing file. The staging hard link is between new copies only; preserved originals and stored KVA files never share an inode with an editable native working file. Failed or unsupported publication returns an error and does not launch the native application.

### Local request boundary

Tests reject foreign Host/Origin values and cross-site requests, bound both declared and chunked request bodies, and check managed-path containment. Source and job download routes resolve opaque IDs. The CLI allows loopback addresses only. This is a single-owner local application, not a qualified remote service.

## Browser gate

The optional `scripts/ui_smoke.mjs` starts its own temporary server and data directory, generates two clips with audio, and exercises the complete browser workflow. It also checks draft/conflict handling, HTML-as-text rendering, real playback/looping, downloads, playlists, archive, and responsive layout.

The local environment could not run a browser content process. Chromium installation returned truncated archives. Firefox downloaded successfully but failed after Juggler startup with `/proc/self/uid_map: EPERM` and content-process signal 11 exits. The attempts were stopped without changing sandbox controls. **No local real-browser or screenshot pass is claimed.**

GitHub Actions includes a Python 3.12 Firefox run using Playwright 1.62.1. Its result is pending the first remote push; the PR will record that result before this gate is claimed as passed. The Python 3.11/3.12 matrix also runs source checks, real media tests, ticket validation, wheel build, and installed-asset verification.

## Explicit remaining qualification

- **Native Windows/Kinovea execution:** unrun. Configured capability and mocked command/filesystem tests do not prove a native drawing/sidecar round trip. EFA-041 owns live qualification. Working directories require no-overwrite hard-link publication support; unsupported filesystems receive an actionable failure.
- **Actual football-cv execution, detector weights, calibration quality, position accuracy, and overlays:** unrun. M1 validates and stores operator-prepared suggestions only. EFA-029–039 define execution and evaluation gates.
- **macOS/Windows installation and desktop launchers:** source/package paths are documented; local execution evidence here is Linux/Python 3.12. Platform qualification is EFA-040/041.
- **Precise frame navigation for variable-rate media:** not claimed. Initial browser stepping uses nominal frame intervals; EFA-020 handles exact navigation.
- **Backup/restore, migrations beyond schema 1, scale, and a representative owner-media matrix:** dedicated later tickets. A restart test is not a disaster-recovery test. EFA-042 is a high-priority next step after M1.
- **KVA semantics/source binding:** safe XML-envelope validation preserves bytes. It does not prove the annotations belong to this source or translate drawings into browser exports.

No real game footage, model weights, credentials, or runtime database is part of the source commit. The original Apache-2.0 `LICENSE` is unchanged; upstream ownership and optional dependencies are documented separately.
