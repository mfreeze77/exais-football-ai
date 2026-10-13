# Design decisions

These decisions describe the agreed implementation direction. “Adopted” means a design rule in the first-milestone contract; it does not mean every supporting feature has completed qualification. Exact capability status is in the [ticket catalog](tickets/catalog.json).

## D-001 — Keep the first application local and single-owner

**Status:** Adopted for M1.

The API binds loopback and the UI is served from the same origin. Python/FastAPI and SQLite provide the first runtime; the UI uses packaged browser assets without a frontend build service or remote CDN. This keeps the owner's film workflow independent of a hosted account or infrastructure stack.

Consequences: LAN/public access, authentication, remote sharing, provider uploads, and billing are outside scope. Native launchers can improve startup later without changing the application's core contracts. [EFA-001](tickets/EFA-001.md), [EFA-014](tickets/EFA-014.md), [EFA-040](tickets/EFA-040.md), [EFA-041](tickets/EFA-041.md).

## D-002 — Preserve source bytes and use derivative views

**Status:** Adopted for M1.

An imported file is hashed and stored intact. Tightening changes the selected source-relative interval. MP4 exports, previews, and overlays are derived artifacts. A source hash identifies bytes, not a player.

Consequences: no M1 source-delete endpoint; jobs must not overwrite originals. Several alternative cuts can later share one explicit source asset through a migration that preserves existing clip IDs. [EFA-002](tickets/EFA-002.md), [EFA-003](tickets/EFA-003.md), [EFA-019](tickets/EFA-019.md).

## D-003 — Use original-source time as the common reference

**Status:** Adopted for M1.

Clip selections and notes use seconds relative to the original imported video. Exported playback time must be translated back through the recorded selection. Frame indices and proxy coordinates require an explicit timebase and transform before later analysis can use them.

Consequences: trimming does not shift saved note times. Variable-frame-rate navigation and overlays have dedicated acceptance work instead of silently assuming frame/fps arithmetic. [EFA-005](tickets/EFA-005.md), [EFA-020](tickets/EFA-020.md), [EFA-033](tickets/EFA-033.md).

## D-004 — Protect review metadata with versions and history

**Status:** Adopted for M1.

Editable clips require a current version and retain revision snapshots. Restore creates a new revision. Relationships are validated centrally in the service/database boundary.

Consequences: two tabs can produce a visible conflict. The UI preserves unsaved edits and the server rejects stale writes. Batch editing later needs a preview and a version-aware recovery path. [EFA-004](tickets/EFA-004.md), [EFA-018](tickets/EFA-018.md).

## D-005 — Keep human meaning separate from model suggestions

**Status:** Adopted for M1.

Human tags, coaching notes, review status, roster identity, and provider suggestions are different records. A position bundle starts unreviewed and retains its raw contents after acceptance/rejection.

Consequences: accepting “LB” does not fill a player name or label a successful tackle. Track corrections and roster mappings later reference run, clip, track, and time interval explicitly. [EFA-013](tickets/EFA-013.md), [EFA-035](tickets/EFA-035.md), [EFA-036](tickets/EFA-036.md).

## D-006 — Integrate Kinovea as an external application

**Status:** Adopted for M1.

Kinovea remains separately installed. The first adapter reports capability, launches a configured Windows executable with a managed path, and preserves validated KVA bytes. Official platform/version findings and GPL v2 source boundaries are recorded in [upstream research](UPSTREAM_RESEARCH.md#kinovea).

Consequences: macOS library support does not imply macOS Kinovea support. Safe KVA preservation is delivered before semantic translation; versioned mapping, reconciliation, and live Windows acceptance remain specific tickets. [EFA-012](tickets/EFA-012.md), [EFA-026](tickets/EFA-026.md), [EFA-027](tickets/EFA-027.md), [EFA-041](tickets/EFA-041.md).

## D-007 — Keep football-cv optional, pinned, and externally configured

**Status:** Adopted boundary for M1; full execution planned in M3.

The core app does not depend on torch/model weights. It validates a narrow positions-import envelope. Full toolkit execution later receives explicit source/calibration/run paths in a separately configured environment. The reviewed revision and detector/documentation drift are preserved in [upstream research](UPSTREAM_RESEARCH.md#football-cv).

Consequences: no automatic model downloads or inference at startup. The runner records actual backend/device/weights; the default temporary upstream data directory is not suitable for durable records. [EFA-013](tickets/EFA-013.md), [EFA-029](tickets/EFA-029.md), [EFA-032](tickets/EFA-032.md).

## D-008 — Use frozen jobs and verified media publication

**Status:** Adopted for M1.

An export captures clip version and interval when queued. Bounded FFmpeg work produces a temporary result; only verified output is published. Source audio is mapped when present.

Consequences: edits after enqueue do not change the job. Errors and interrupted runs remain failed states with useful reasons. The upstream position renderer needs a separate audio/timing adaptation before its output can satisfy this rule. [EFA-011](tickets/EFA-011.md), [EFA-034](tickets/EFA-034.md).

## D-009 — Keep a small required metadata surface

**Status:** Adopted for M1.

The owner can start with a title, an optional game/player context, and a few tags. Extra football dimensions are optional. A playlist stores ordered clip references instead of making video copies.

Consequences: the library is useful before a full playbook taxonomy exists. Later presets/aliases improve consistency without imposing one universal scheme. Saved searches and review sessions extend existing filters/playlists. [EFA-007](tickets/EFA-007.md), [EFA-010](tickets/EFA-010.md), [EFA-017](tickets/EFA-017.md), [EFA-024](tickets/EFA-024.md).

## D-010 — Treat recovery as a product capability

**Status:** Planned high-priority extension after M1.

Restart persistence does not equal disaster recovery. A complete backup must include consistent database state plus the originals and non-reproducible review assets, with explicit handling of in-flight jobs and optional caches.

Consequences: backup/restore can begin while advanced CV work is pending. Migration and cleanup features depend on recoverable data and may not erase sources to make room. [EFA-042](tickets/EFA-042.md), [EFA-044](tickets/EFA-044.md), [EFA-045](tickets/EFA-045.md).

## D-011 — Preserve upstream ownership and dependency provenance

**Status:** Adopted for M1.

The repository retains its Apache-2.0 license for original project code. Kinovea is external GPL v2 software. The pinned football-cv source declares MIT; model dependencies and weights remain separately governed. No unlicensed or GPL/AGPL source from earlier shortlist projects is copied into this application.

Consequences: every future reused source component or distributed dependency needs a provenance/license entry. Pin changes require adapter-fixture and capability review. [EFA-047](tickets/EFA-047.md), [upstream source/license evidence](UPSTREAM_RESEARCH.md).

## D-012 — Distinguish implementation, qualification, and accepted merge

**Status:** Adopted for all milestones.

Stable EFA IDs, complete ticket bodies, schemas, a source registry, and GitHub mappings make the work traceable. “Implemented” means an evidenced candidate; “done” requires accepted merge. Native Windows and actual CV inference are separately qualified capabilities.

Consequences: generated real-media tests, installed-package checks, and honest environment limitations matter alongside unit tests. No PR is merged automatically, and no ticket closes merely because a function exists. [EFA-015](tickets/EFA-015.md), [EFA-016](tickets/EFA-016.md), [EFA-048](tickets/EFA-048.md), [status rules](tickets/README.md#status-and-completion-rules).
