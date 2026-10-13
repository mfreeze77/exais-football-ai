# Implementation tickets

This backlog translates the agreed personal film workflow into **48 distinct implementation tickets and 255 concrete acceptance checks**. The scope starts with existing American-football clips and covers tightening, labeling, organization, future watching, external Kinovea review, and optional football-cv analysis. It does not assume a hosted service or a commercial multi-tenant product.

The [catalog](catalog.json) is the machine-readable status and GitHub issue mapping source. Each linked ticket is its reviewable implementation body. The [roadmap](../ROADMAP.md) explains ordering, and [IMPLEMENTATION_CONTRACT.md](../IMPLEMENTATION_CONTRACT.md) governs the first implementation. A ticket ID is stable even if the GitHub issue number differs.

## Status and completion rules

| Status | Meaning |
| --- | --- |
| `planned` | Scoped and ready to sequence; implementation is not claimed. |
| `in_progress` | Work has started; acceptance has not yet been qualified. |
| `implemented` | The candidate implementation has recorded qualification evidence for its stated scope and awaits accepted merge. Optional platform/model gates can remain explicitly unrun in separate tickets. |
| `blocked` | A concrete dependency or required environment prevents progress; record the reason and next action in evidence. |
| `done` | Acceptance evidence is recorded and the accepted change is merged. Close the mapped GitHub issue at this point. |

An implementation claim requires an evidence entry identifying the code revision, executed checks, environment, and remaining limits. A green unit test does not establish native Windows behavior, model accuracy, or real-media behavior. EFA-012 and EFA-013 deliberately qualify adapter contracts in M1; native Kinovea and full model execution have their own tickets.

Keep `status`, `evidence`, and `github_issue` aligned in the catalog and Markdown body. Preserve imported suggestions even when a human rejects them. No ticket closure can be inferred from a README claim or a drafted plan.

## Milestones and priority

| Milestone | Tickets | Objective |
| --- | --- | --- |
| M1 | EFA-001–016 | Working local library, immutable originals, trim/labels/notes/search/playlists/export, integration adapters, qualification, and installable package. |
| M2 | EFA-017–028 | Faster review, richer catalog operations, comparisons/annotations, and versioned Kinovea reconciliation. |
| M3 | EFA-029–039 | Reproducible external CV, reviewed calibration, tracked artifacts, audio-preserving overlays, correction/identity decisions, and measured quality. |
| M4 | EFA-040–048 | Desktop convenience, backup/restore, portable data, migration/storage hardening, scale, and release qualification. |

P0 is needed to protect or complete the core owner workflow; P1 is the next substantive feature/qualification work; P2 is an enhancement that depends on earlier evidence. Milestones group outcomes and are not a requirement to postpone all recovery work until after AI. In particular, **EFA-042 backup/restore should start after M1 as soon as its dependencies are satisfied**. macOS/Windows packaging can also proceed independently of the model pipeline.

## Ticket index

Current statuses and issue links live in each ticket body and the catalog so this overview does not introduce a second manually maintained status table.

| ID | Ticket | Milestone | Priority | Dependencies |
| --- | --- | --- | --- | --- |
| [EFA-001](EFA-001.md) | Establish the local application and capability boundary | M1 | P0 | — |
| [EFA-002](EFA-002.md) | Persist film records and immutable source provenance | M1 | P0 | EFA-001 |
| [EFA-003](EFA-003.md) | Import existing clips safely with duplicate detection | M1 | P0 | EFA-002 |
| [EFA-004](EFA-004.md) | Save metadata with optimistic concurrency and revision restore | M1 | P0 | EFA-002 |
| [EFA-005](EFA-005.md) | Review source video with a non-destructive selected interval | M1 | P0 | EFA-003, EFA-004 |
| [EFA-006](EFA-006.md) | Add durable timed coaching notes | M1 | P0 | EFA-002, EFA-005 |
| [EFA-007](EFA-007.md) | Find film by search and combined football filters | M1 | P0 | EFA-003, EFA-004 |
| [EFA-008](EFA-008.md) | Manage games and a simple manually maintained roster | M1 | P0 | EFA-002, EFA-004 |
| [EFA-009](EFA-009.md) | Favorite and archive clips without losing film | M1 | P0 | EFA-004, EFA-007 |
| [EFA-010](EFA-010.md) | Create ordered playlists and watch a focused set of clips | M1 | P0 | EFA-005, EFA-008, EFA-009 |
| [EFA-011](EFA-011.md) | Run durable audio-preserving trim exports | M1 | P0 | EFA-002, EFA-005 |
| [EFA-012](EFA-012.md) | Provide safe external Kinovea launch and KVA preservation | M1 | P0 | EFA-001, EFA-003 |
| [EFA-013](EFA-013.md) | Import and review football-cv position suggestions safely | M1 | P0 | EFA-002, EFA-003, EFA-004 |
| [EFA-014](EFA-014.md) | Deliver the complete same-origin local film-room interface | M1 | P0 | EFA-003, EFA-004, EFA-005, EFA-006, EFA-007, EFA-008, EFA-009, EFA-010, EFA-011, EFA-012, EFA-013 |
| [EFA-015](EFA-015.md) | Qualify source safety and the complete local media workflow | M1 | P0 | EFA-003, EFA-004, EFA-005, EFA-006, EFA-011, EFA-012, EFA-013, EFA-014 |
| [EFA-016](EFA-016.md) | Package the first release and publish an honest handoff | M1 | P0 | EFA-001, EFA-002, EFA-014, EFA-015 |
| [EFA-017](EFA-017.md) | Introduce reusable football tag groups and terminology | M2 | P1 | EFA-004, EFA-007, EFA-016 |
| [EFA-018](EFA-018.md) | Apply batch metadata edits with preview and recovery | M2 | P1 | EFA-004, EFA-009, EFA-014, EFA-017 |
| [EFA-019](EFA-019.md) | Support alternative cuts and multiple views of one source | M2 | P1 | EFA-002, EFA-004, EFA-005, EFA-011, EFA-016 |
| [EFA-020](EFA-020.md) | Add accurate frame navigation and timeline previews | M2 | P1 | EFA-005, EFA-019 |
| [EFA-021](EFA-021.md) | Qualify keyboard, accessibility, and small-screen review | M2 | P1 | EFA-014, EFA-016 |
| [EFA-022](EFA-022.md) | Compare two clips around explicit synchronization anchors | M2 | P1 | EFA-005, EFA-019, EFA-020 |
| [EFA-023](EFA-023.md) | Create source-anchored drawings and keyframe snapshots | M2 | P1 | EFA-004, EFA-020 |
| [EFA-024](EFA-024.md) | Save searches and maintain dynamic clip collections | M2 | P1 | EFA-007, EFA-010, EFA-017 |
| [EFA-025](EFA-025.md) | Add focused playlist review sessions and progress | M2 | P1 | EFA-010, EFA-019, EFA-024 |
| [EFA-026](EFA-026.md) | Inspect supported KVA versions and propose metadata imports | M2 | P1 | EFA-012, EFA-019, EFA-020 |
| [EFA-027](EFA-027.md) | Reconcile Kinovea and catalog edits without silent data loss | M2 | P1 | EFA-004, EFA-026 |
| [EFA-028](EFA-028.md) | Launch a Kinovea comparison from saved clip pairs | M2 | P1 | EFA-012, EFA-022, EFA-026 |
| [EFA-029](EFA-029.md) | Qualify a pinned external football-cv environment | M3 | P1 | EFA-001, EFA-013, EFA-016 |
| [EFA-030](EFA-030.md) | Create manual field calibration with validated geometry | M3 | P1 | EFA-019, EFA-020, EFA-029 |
| [EFA-031](EFA-031.md) | Review analysis suitability and pre-snap context | M3 | P1 | EFA-020, EFA-030 |
| [EFA-032](EFA-032.md) | Execute bounded, resumable external analysis jobs | M3 | P1 | EFA-011, EFA-029, EFA-030, EFA-031 |
| [EFA-033](EFA-033.md) | Import full analysis artifacts with a versioned manifest | M3 | P1 | EFA-013, EFA-019, EFA-032 |
| [EFA-034](EFA-034.md) | Render reviewed position overlays with source audio and timing | M3 | P1 | EFA-011, EFA-020, EFA-033, EFA-035 |
| [EFA-035](EFA-035.md) | Correct suggested positions through a human review ledger | M3 | P1 | EFA-013, EFA-033 |
| [EFA-036](EFA-036.md) | Link tracks to roster players through explicit scoped review | M3 | P1 | EFA-008, EFA-033, EFA-035 |
| [EFA-037](EFA-037.md) | Evaluate position quality on representative owner film | M3 | P1 | EFA-029, EFA-030, EFA-033, EFA-034, EFA-035, EFA-036 |
| [EFA-038](EFA-038.md) | Transfer calibration between compatible plays with review | M3 | P2 | EFA-030, EFA-031, EFA-033, EFA-037 |
| [EFA-039](EFA-039.md) | Detect and review tracking gaps and identity switches | M3 | P2 | EFA-033, EFA-035, EFA-036, EFA-037 |
| [EFA-040](EFA-040.md) | Package a convenient macOS local launcher | M4 | P1 | EFA-016, EFA-021 |
| [EFA-041](EFA-041.md) | Qualify Windows packaging and live Kinovea workflows | M4 | P1 | EFA-012, EFA-016, EFA-021, EFA-026, EFA-027, EFA-028 |
| [EFA-042](EFA-042.md) | Back up and restore a complete film library | M4 | P0 | EFA-002, EFA-010, EFA-011, EFA-012, EFA-013, EFA-016 |
| [EFA-043](EFA-043.md) | Export portable metadata and selected film packages | M4 | P1 | EFA-011, EFA-016, EFA-042 |
| [EFA-044](EFA-044.md) | Harden database migration and interrupted-write recovery | M4 | P1 | EFA-002, EFA-011, EFA-016, EFA-042 |
| [EFA-045](EFA-045.md) | Show storage health and safely reclaim derived caches | M4 | P1 | EFA-009, EFA-011, EFA-042, EFA-044 |
| [EFA-046](EFA-046.md) | Qualify larger libraries and a representative media matrix | M4 | P1 | EFA-007, EFA-011, EFA-014, EFA-020, EFA-044 |
| [EFA-047](EFA-047.md) | Maintain dependency provenance and upstream upgrade gates | M4 | P1 | EFA-016, EFA-029 |
| [EFA-048](EFA-048.md) | Run a complete owner-workflow release qualification | M4 | P1 | EFA-021, EFA-022, EFA-023, EFA-025, EFA-027, EFA-034, EFA-035, EFA-036, EFA-037, EFA-040, EFA-041, EFA-042, EFA-043, EFA-044, EFA-045, EFA-046, EFA-047 |

## Schemas and validation

- [ticket.schema.json](ticket.schema.json) describes each ticket.
- [catalog.schema.json](catalog.schema.json) describes the complete catalog and source registry.
- [validate.py](validate.py) checks the committed schema subset with the Python standard library, then verifies body paths/headings/acceptance checks, unique IDs/titles/issue mappings, required evidence for implemented/done states, source references, and the dependency DAG.

From the repository root:

```bash
python docs/tickets/validate.py
python docs/tickets/validate.py --show-order
```

Validation performs no network writes. It rejects missing dependencies, cycles, unsafe body paths, missing files, unknown schema fields, or inconsistent GitHub number/URL pairs. The validator deliberately implements only the JSON Schema vocabulary used by these committed schemas; external tooling can use a full Draft 2020-12 validator.

## Working a ticket

1. Read the ticket, governing contract, relevant architecture/decisions, and pinned upstream sources.
2. Confirm prerequisites and record any qualification environment that is unavailable.
3. Implement the complete behavior with focused verification tied to acceptance checks.
4. Record actual commands/results, source/data provenance where relevant, and meaningful limitations.
5. Update the catalog and ticket together, run validation, and open/update the reviewable PR.
6. Mark done and close the mapped issue after accepted merge; do not merge automatically.

Create GitHub issues from the ticket bodies and store the returned numeric ID and canonical URL under `github_issue`. Do not assume EFA-001 is GitHub issue #1, and do not create duplicate issues when rerunning the mapping process.
