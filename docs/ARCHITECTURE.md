# Architecture

## Scope and source of truth

The first milestone is a single-owner local application with Python 3.11+, FastAPI, SQLite, FFmpeg/ffprobe, and packaged browser assets. [IMPLEMENTATION_CONTRACT.md](IMPLEMENTATION_CONTRACT.md) defines exact routes, fields, module contracts, and M1 acceptance. This document explains those boundaries and the intended extension points. [Ticket status](tickets/catalog.json) and executed qualification determine what is actually supported.

The architecture reuses mature media tools and external analysis applications through controlled interfaces. Original Apache-2.0 application code owns the catalog, validation, local API/UI, jobs, and adapter envelopes. Kinovea remains external. football-cv runs only through an optional separately configured environment when its execution milestone is implemented.

## Component responsibilities

| Component | Responsibility | Boundary |
| --- | --- | --- |
| CLI/configuration | Load the managed data directory, bind loopback, validate executable configuration, start/stop the application. | User configuration supplies allowed local settings; request fields cannot choose executables. |
| FastAPI routes/models | Parse requests, enforce supported schemas, serve same-origin assets/media, return stable errors. | HTTP transports IDs and bounded data, not arbitrary filesystem commands. |
| Catalog service | Implement clip/roster/game relationships, versioned edits, notes, searches, playlists, and analysis review. | Centralizes invariants so UI shortcuts cannot bypass them. |
| SQLite persistence | Store durable metadata, immutable review snapshots, relationships, and job states. | Transactions protect related writes; source files remain separate managed assets. |
| Managed file storage | Retain originals, KVA files, generated previews, exports, and later analysis runs. | Storage names are generated; display filenames do not become trusted paths. |
| Export worker | Claim frozen export requests, supervise processing, verify output, record terminal state. | Failed/interrupted work must remain distinguishable from completed artifacts. |
| Media module | Probe media, generate a preview, and accurately export H.264 MP4 with source audio when present. | Argument-array subprocesses, bounded time/resources, checked return codes. |
| Kinovea adapter | Report platform/configuration capability, validate KVA envelopes, construct safe native launches. | External Windows process; no copied GPL implementation or initial two-way sync. |
| football-cv adapter | Validate imported source-bound suggestions; later supervise a pinned toolkit run. | Optional external environment; provider output is separate from human labels. |
| Browser UI | Present catalog/review controls and guide the full import-to-export journey. | Packaged same-origin assets, safe text rendering, preserved unsaved state. |

## Important record boundaries

| Record | Identity and lifecycle | Invariant |
| --- | --- | --- |
| Clip | Opaque UUID; editable metadata is versioned. | Stable association with the original media hash and duration. |
| Original media | Imported bytes, source SHA-256, probe facts, original display filename. | Never rewritten by trimming, metadata edits, notes, or analysis. |
| Selected interval | Source-relative start/end on a clip revision. | Finite values with 0 <= start < end <= source duration. |
| Game | Opaque ID plus title/opponent and optional contextual fields. | A nonexistent game cannot be assigned to a clip. |
| Roster player | Opaque ID plus manually supplied name/jersey/position. | A jersey is an attribute, not a globally unique identifier. |
| Human tags | User-authored categorization associated with a clip. | Provider suggestions do not silently populate them. |
| Note | Clip ID and optional source-relative moment. | The note cannot be removed through another clip's route. |
| Revision | Snapshot of editable clip state. | Restoring creates a newer revision and preserves prior history. |
| Playlist | Ordered clip references. | Membership does not copy or mutate original files. |
| Job | Frozen clip revision plus durable execution state. | Later metadata edits do not alter already-enqueued output intent. |
| KVA sidecar | Original bytes, validation result, associated clip. | Safe XML parsing is not a claim of complete semantic translation. |
| Analysis suggestion | Source hash, upstream revision, track-to-position mapping, review state. | Raw payload remains preserved; track IDs never imply roster identity. |

M1 stores one original source association per clip. EFA-019 introduces an explicit source-asset/view distinction if needed for multiple named cuts of the same source. That migration must retain existing IDs and source-relative timing. The design should not create extra copies merely to support several categorized views.

## Import and publication

Import is a staged operation: receive bounded bytes into a managed temporary location, compute the source hash and check for a duplicate, probe new source bytes as media, then publish the managed source and record under a write transaction. Any failure must leave either a valid complete import or a documented cleanup/recovery state, not a half-valid clip. Once a content-addressed original is published, catalog rollback retains it so a concurrent successful import cannot lose a reused file. Subsequent imports verify retained bytes before reuse; automatic orphan cleanup remains a recovery ticket.

The client filename is retained for display. It must not dictate a destination path. Resolving an ID to a managed path is a server responsibility, and containment is checked after resolution. Both API validation and subprocess construction must treat uploaded media as untrusted input.

The thumbnail is a disposable derived convenience. A missing thumbnail cannot cause the source to disappear or falsely signal that the video itself was imported successfully. A duplicate returns an existing clip ID explicitly; it does not silently replace that clip's title or tags.

## Editing and concurrency

An OS-level instance lock permits one running app to own each data directory, avoiding competing export workers. Every editable clip request includes its current integer version. A successful transaction applies validated relationships and interval bounds, saves a revision, increments the version, and updates the modification time. A stale version returns 409. The UI preserves the user's unsaved values and explains the conflict. Playlist order currently uses whole-list replacement without a version token; shared multi-user playlist editing is outside M1.

Tag/player arrays use replacement semantics in M1. The UI therefore sends the complete intended list rather than interpreting a partial selection as an append. EFA-018 adds explicit add/remove/replace operations for batches, with preview and recovery.

History restore is another guarded edit. It takes the requested historical version and current version, restores only editable metadata, and creates a new revision. It cannot replace a source hash or media path.

## Media time and coordinate systems

The application's authoritative timeline is the imported source timeline. All editable times use seconds relative to that source. An export may begin at playback time zero, so it must retain the relation:

`source_time = selected_start_seconds + exported_time`

This relation assumes an ordinary rate-preserving trim. Later speed changes, freezes, concatenations, proxies, or timestamp remapping require an explicit piecewise transform, not silent reuse of this simple formula.

For planned CV artifacts, pixel boxes must identify their source dimensions, orientation/crop transform, and frame/timestamp basis. Field coordinates must identify units, origin, and calibration revision. A box from a resized proxy is not directly usable on the original. Variable-frame-rate videos require presentation timestamps before precise overlays or frame stepping can be qualified.

Browser playback convenience and verified rendering are separate capabilities. EFA-020 establishes accurate decoded-frame navigation; EFA-034 qualifies tracking overlays. Neither follows automatically from a successful basic video element.

## Export jobs

M1 jobs have four states:

| State | Meaning | Allowed next outcome |
| --- | --- | --- |
| queued | A durable request exists with frozen settings. | running or failed on recovery. |
| running | The owned worker is processing that request. | complete or failed. |
| complete | Verified output was published and is downloadable. | Remains complete until a later explicit retention feature removes its cached file. |
| failed | A meaningful failure/interruption was recorded. | A future retry creates a new attempt; no silent success transition. |

The worker snapshots the selected interval and clip version when the export is requested. It uses bounded local-only FFmpeg execution with no shell, preserves source audio when available, and verifies the result before atomic replacement of the final output path. An interrupted process or restart must not make a partial MP4 downloadable as completed work.

Cancellation, richer retries, resumable stage execution, and model job supervision belong to EFA-032. The first build intentionally reports interruption honestly instead of implementing an unreliable hidden resume.

## Kinovea interoperability

The M1 bridge is a launch/sidecar boundary. It accepts only a configured executable, uses a managed video path, and runs only where the capability is supported. Unsupported platforms return a reason. KVA content is size-bounded, parsed without DTD/entities, checked for a supported root/envelope, and retained byte-for-byte.

Official Kinovea 2023.1 release notes document working-zone persistence in annotation files; 2025.1 documents the second-video launch option. That establishes useful future interoperability targets, not an existing universal synchronization API. [Sources and version boundaries](UPSTREAM_RESEARCH.md#kinovea).

Planned KVA processing is layered:

1. Preserve raw sidecars and establish a supported version.
2. Read recognized fields into a proposal with source/time-origin mappings.
3. Show differences and conflicts against the current catalog revision.
4. Apply only accepted changes as new catalog revisions.
5. Preserve unsupported elements and previous sidecar versions.

A drawing burned into a derived video cannot become an editable native Kinovea object without a separately implemented adapter. The application will not pretend that metadata notes automatically appear in an exported movie.

## football-cv boundary

M1 imports a narrow known envelope: source SHA-256, upstream revision, position mapping, and optional notes. It validates the association and supported tokens, stores the raw payload, and exposes explicit unreviewed/accepted/rejected states. Human review of a bundle does not create a roster mapping.

The later runner snapshots persistent data directory, stable play ID, source, calibration, selected context, provider revision, detector/weights, and settings. This addresses the upstream API's temporary-directory default. The configured external Python environment is chosen locally; HTTP requests cannot submit an executable or shell command. [Pinned API](https://github.com/andrewneuwirth/football-cv/blob/38f6f758d3e78af3b700f144214c2d4566459c5d/footballcv/api.py).

A future full manifest binds every artifact to its source and run. Correction and identity layers are append-only reviewed decisions over those artifacts. A new run cannot inherit a roster identity merely because track “24” appears again. Derived rendering consumes effective reviewed labels with their revision, not an overwritten provider JSON file.

## Local trust and runtime boundaries

The server binds loopback and serves the browser UI from the same origin. It must not grant wildcard cross-origin control. Local-origin/host handling, bounded request sizes, safe DOM rendering, path containment, strict XML/JSON validation, and subprocess isolation remain necessary even for a local application.

Do not expose arbitrary URL ingestion, arbitrary file browsing, arbitrary executable paths, or a source-delete route through the API. Do not add model downloads or provider uploads as startup side effects. Film, databases, logs, local configuration, exported media, and weights stay out of git.

Loopback is not a multi-user authorization design. Adding LAN/public access would require a new scoped architecture and authorization decision; it is not enabled by changing the bind address in this release.

## Recovery and future growth

The highest-priority recovery extension is a consistent database snapshot plus a file manifest that can restore a complete library into a new workspace. Source film, human decisions, and preserved sidecars are authoritative assets; previews are generally reproducible caches. Some historical analysis runs may be expensive or impossible to recreate and need explicit retention classification.

Later migration and disk-failure work must coordinate SQLite transactions with filesystem publication. SQLite atomicity cannot alone make a half-written MP4 complete. Recovery tests use copied/generated fixtures, not the only copy of the owner's library.

A move to multiple owners, a remote database, or a hosted processing service is not part of this roadmap. The current interfaces leave room for future work without inventing those requirements now.
