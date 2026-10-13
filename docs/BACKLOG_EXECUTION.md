# Remaining backlog implementation

This branch continues the owner's request to finish EFA-018 through EFA-048. It is based on the reviewed EFA-017 branch while PR #50 remains unmerged. No merge authorization for the new work is assumed.

## Delivery boundaries

Implementation includes the catalog, browser UI, media workflow, optional external computer vision, interoperability, recovery, platform packaging, and tests together. Acceptance evidence must name the executed revision and environment. No ticket is complete merely because its API exists.

The first local checkpoint preserves the 124-test taxonomy baseline and adds passing film-study and recovery tests. These tests exercise previewed atomic bulk edits, undo, independent cuts sharing one original, saved searches, frozen study sessions, comparison metadata, drawing revisions, decoded-frame indexing, verified backup/restore, portable packages, cleanup protection, and durable work attempts.

The complete code candidate and browser qualification are in progress. This document is not a release certificate. Real-model accuracy, representative owner-film acceptance, native Kinovea execution, and platform packaging must retain explicit unqualified status until actually exercised.

## Invariants

- Originals and immutable analysis remain preserved.
- Human position corrections and roster identity remain separate from model suggestions.
- Calibration changes require review; external tools receive verified working copies.
- Model acquisition and environment setup are deliberate operations, never startup side effects.
- Restoration uses a verified staging directory and a new destination, never overwrites a live film library.
- Completed features, executed tests, and outstanding environment gates are reported separately.
