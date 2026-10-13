# EXAIS Football AI product specification

## Purpose

EXAIS Football AI is a local film room for a person who already cuts American-football games into smaller clips. It makes those clips easier to tighten, describe, organize, study, and find again. Detailed technique review can continue in an externally installed Kinovea application. Optional football-cv analysis can later add reviewed position overlays and track information.

The complete product workflow is:

**Import existing film → choose the useful interval → title and categorize it → add observations → revisit a player, skill, or collection → optionally export or analyze it.**

This document defines intended behavior, not a claim that every feature is delivered. The [ticket catalog](tickets/catalog.json) records implementation state, and the repository's [README](../README.md) records the qualified delivery. The [first-milestone contract](IMPLEMENTATION_CONTRACT.md) is the authoritative API and implementation boundary.

## The owner's actual job

The owner already has the source clips. The missing value is a repeatable way to refine and reuse them. A typical session begins with a folder of plays from a game and ends with useful film collections, such as:

- A player's repetitions from that game.
- Good examples of a specific movement or technique.
- Plays that need another look before practice.
- The same skill across multiple games.
- A short ordered film session with a note at each important moment.

A title describes the individual play. Tags make it discoverable in several contexts. Game/player relationships keep identity and event context out of fragile filenames. Notes explain what the owner saw; review status expresses the owner's decision.

A hypothetical clip titled “Week 4 — pursuit angle before contact” might have phase “defense,” tags “pursuit” and “tackling,” a manually chosen roster player, and a note at source time 8.4 seconds. The same underlying clip can appear in player, game, skill, and watch-again views without extra source copies. This is an example of the product's metadata model, not a real clip or generated coaching judgment.

## Core product rules

1. **Original film survives every edit.** A selected interval and an exported trim are derivatives. There is no first-release endpoint that deletes the source.
2. **Timing always has an explicit source.** Saved in/out values, notes, and analysis associations use seconds relative to the imported original. An exported video starting at zero does not redefine them.
3. **Human labels and suggestions are separate.** An imported model result cannot silently become a roster identity, a play call, a technique tag, or an approved coaching assessment.
4. **Optional tools stay optional.** The library must function without Kinovea, a GPU, a detector environment, or model weights.
5. **Review work is durable.** Metadata, notes, relationships, history, and sidecars survive a normal restart. Full disaster recovery is a separately qualified backup/restore feature.
6. **The owner controls external processing.** Starting the local app does not upload film or download/run a model.
7. **The interface makes incomplete work visible.** An unavailable dependency, failed job, unsupported format, or unreviewed suggestion cannot be presented as success.

## First milestone: one complete useful journey

### Import and establish context

The owner can create a game with its opponent and optional date/season, create simple roster records, and import existing video files into application-managed storage. Each source is probed and hashed. Duplicate bytes return the existing clip with an explicit duplicate indication. A malicious filename, incomplete upload, or failed decoder must not create a usable-looking record.

The owner can begin without a game or player assignment and add context later. Required metadata should remain small; this is a personal review tool, not a data-entry exercise.

### Tighten the viewing interval

The review page plays the original source, lets the owner mark in and out, loops the selected interval, and saves the selection without touching source bytes. A wider pre-snap setup remains available in the original even when the preferred watching interval is shorter.

M1 browser seeking is not advertised as universally frame-accurate. Precise decoded-frame navigation, a variable-frame-rate timestamp index, and alternative named cuts are later work. Exact MP4 export is independently checked through FFmpeg.

### Label and review

The owner edits a title, description, phase, tags, manually assigned players, favorite/archive flags, and review status. The supported M1 review states are new, review, approved, and needs_work. These are user decisions; they are not model quality scores.

Edits require the current record version. A second tab cannot silently replace changes made by the first. The owner can inspect history and restore earlier editable metadata as a new revision. Timed notes attach an observation to the source moment and remain associated with the correct clip.

### Find and watch again

Search and filters combine game, player, tag, phase, review state, favorite, and archive selection. The default library shows active clips. Archive changes visibility, and the same source remains recoverable.

A playlist is a named ordered list of clip IDs. It is useful for a focused film session and does not copy footage. M1 fixed playlists use each clip's current selected interval; version-frozen sessions and dynamic saved-search collections are later extensions.

### Export a useful clip

The owner requests a trim export. The job captures the exact clip version and interval at enqueue time, so later edits do not change that pending request. A completed export provides a downloadable H.264 MP4 and retains source audio when present.

Queued, running, complete, and failed states remain visible. A restart cannot make interrupted work look completed. Output becomes available only after processing and verification succeed. The original file hash must remain unchanged.

## Kinovea: detailed external review

Kinovea's official documentation describes video observation, comparison, annotation, and motion measurement. Its official download page specifies Windows x86-64 and excludes Apple Silicon support. It remains a separate application; EXAIS Football AI's browser library can be used on a Mac even when Kinovea is unavailable. See [upstream research](UPSTREAM_RESEARCH.md) for primary sources and version details.

The first integration does two specific things:

- Launch a configured local Kinovea executable on a supported Windows system using the managed video path.
- Accept, safely validate, preserve, and return a KVA sidecar associated with a clip.

M1 does not read a KVA working zone into the library's trim, translate all drawings, or synchronize catalog tags. Those operations require release-specific fixtures and reviewable mapping. The planned progression is KVA inspection, proposed metadata changes, conflict-aware reconciliation, and comparison launch. An unknown XML element remains preserved in the original sidecar; it must not be erased just because this app does not understand it.

Native Windows execution is a separate acceptance gate. Unit tests of command construction and unsupported-platform states are useful adapter evidence but do not establish that a real Kinovea window opened.

## Optional football-cv analysis

The initial analysis boundary accepts an external positions bundle with source SHA-256, upstream revision, and run-local tracking IDs mapped to supported broad positions. Every imported bundle begins unreviewed. The owner can accept or reject the bundle while its original contents stay intact.

Full model execution is later work. It requires an isolated pinned environment, reviewed calibration, sufficient pre-snap/field context, bounded background jobs, complete artifact provenance, correction tools, and evaluation on representative owner-approved film.

The intended meanings must stay distinct:

| Record | Meaning |
| --- | --- |
| Raw detection/track | The provider observed a candidate participant at a location/time. |
| Suggested position | The provider inferred a broad alignment-based role. |
| Accepted/corrected position | The owner reviewed that position in the relevant run. |
| Roster assignment | The owner explicitly linked a scoped appearance to a known roster record. |
| Play/technique tag | The owner categorized what the clip shows. |
| Coaching note | The owner recorded an observation to revisit. |

An accepted “LB” suggestion does not establish the player's name, a correct assignment, or a successful tackle. New runs can reuse numerical track IDs for different participants. Automatic identity propagation is outside the agreed first build.

The inspected football-cv renderer drops source audio, and the detector implementation differs from the README wording. These are integration tasks, not reasons to rewrite a detector from scratch. The reuse boundary is a documented external toolkit with pinned inputs/outputs and project-owned validation. [UPSTREAM_RESEARCH.md](UPSTREAM_RESEARCH.md) records the exact inspected code.

## Planned extensions

| Product need | Tickets |
| --- | --- |
| Consistent labels, bulk editing, alternative cuts | EFA-017–019 |
| Precise frame study, accessibility, comparison, drawings | EFA-020–023 |
| Saved searches and resumable film sessions | EFA-024–025 |
| KVA interpretation, reconciliation, comparison handoff | EFA-026–028 |
| Optional CV environment, calibration, execution, artifacts | EFA-029–033 |
| Audio-preserving overlays, corrections, reviewed identity | EFA-034–036 |
| Representative evaluation and reliability improvements | EFA-037–039 |
| macOS/Windows delivery | EFA-040–041 |
| Backup/restore, portable exports, migrations, storage | EFA-042–045 |
| Scale/media qualification and reproducible releases | EFA-046–048 |

Backup/restore is high priority once M1 is working. It can proceed while more advanced review or CV work is underway; the milestone group is not a request to postpone protecting the library.

## Delivery and support boundaries

The first delivery is Python/FastAPI, SQLite, FFmpeg/ffprobe, and a same-origin browser interface without a frontend build service or remote runtime assets. It is local and single-owner. Public hosting, billing, organization accounts, team messaging, remote sharing, automatic game downloading, and biometric recognition are outside this build.

Platform support is stated per capability: browser library, media processing, native Kinovea, and each detector environment can have different qualification states. An installed optional dependency does not by itself make its workflow qualified.

A useful release must demonstrate the actual import-to-export journey using generated media with audio, demonstrate restart persistence, verify source hashes, and distinguish executed checks from unavailable native/model gates. Later releases add a restore drill and representative real-film evaluation. Each accepted implementation maps to a ticket and evidence; the issue closes after accepted merge.
