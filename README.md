# Exais Football

**Your clips, tightened and organized for the next film session.**

Exais is a local American-football film library. Import the shorter clips you already make, choose their in/out points, add football labels and coaching notes, then find them again by game, player, phase, tag, or review status. Original footage stays intact. Export a selected interval as an MP4 when you need a separate file.

This first milestone provides a working browser interface backed by Python, SQLite, and FFmpeg. It is an early local application, with optional adapters for external review tools. See [qualification evidence](docs/QUALIFICATION.md) for what has actually been exercised and the remaining platform gates.

## What you can do

- Import existing MP4, MOV, M4V, MKV, AVI, and WebM files. Exact duplicate uploads return their existing clip.
- Save a title, game, manually selected players, phase, review state, tags, favorites, and an archive state.
- Review the source, loop a chosen interval, and save non-destructive in/out points.
- Add source-timed coaching notes, inspect metadata history, and restore an earlier revision.
- Search and combine filters, then build ordered playlists for future study.
- Export a frozen version of the selected interval to H.264 MP4, retaining source audio as AAC. Export jobs record source/output hashes and validation results.
- Preserve Kinovea `.kva` annotation sidecars and prepare a Windows-only native handoff when the executable is configured.
- Import source-bound `football-cv` position suggestions and record your acceptance or rejection separately from human labels.

The app starts with an empty library. It does not download models, upload your footage to a provider, identify named players, or automatically grade technique. Running `football-cv` and qualifying its predictions are later milestones; the current integration accepts an explicitly prepared result bundle.

## Start the film room

Requirements: **Python 3.11 or newer**, and **FFmpeg + ffprobe** available on your `PATH`. Obtain FFmpeg from the [official download page](https://ffmpeg.org/download.html). Browser playback is most reliable with H.264/AAC MP4; the other importable containers may contain codecs your browser cannot decode.

Clone this repository and enter its directory. While the first pull request is under review, check out `build/film-library-foundation`.

### macOS / Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install .
exais-football doctor
exais-football serve
```

### Windows PowerShell

```powershell
py -m venv .venv
.venv\Scripts\python.exe -m pip install .
.venv\Scripts\exais-football.exe doctor
.venv\Scripts\exais-football.exe serve
```

Open **http://127.0.0.1:8765**. Keep the terminal running during your session; press Ctrl+C to stop. No login is required. The service binds only to a loopback address and serves its own scripts and styles.

To choose a media directory or port:

```bash
exais-football serve --data-dir /path/to/your/film-room --port 8765
```

Only one running application should own a data directory. Different film rooms can use different directories and ports. The complete catalog and its media live together in the chosen directory; the default is `~/.exais-football`.

### Your first session

1. Create a game and any players you want to tag.
2. Import your existing clips, optionally assigning the batch to that game.
3. Open a clip. Mark an in and out point, give it a useful title, and add labels such as `run fit`, `press release`, or `pass protection`.
4. Save your changes. Add a timed note at the moment you want to revisit.
5. Search/filter the library or add clips to a playlist such as “Week 3 — things to fix.”
6. Export only when you want a separate trimmed MP4. The saved range already controls review without rewriting the original.

All saved times are relative to the uploaded source file. Exports begin at zero in their new file, but provenance retains the source-relative start and end. Keyboard frame steps in this initial UI are approximate; exact variable-frame-rate navigation has a dedicated follow-up ticket.

See the [UI guide](docs/UI_GUIDE.md) for controls, draft protection, playlists, and integration imports.

## Kinovea and football-cv

### Kinovea: external technique review

[Kinovea](https://github.com/Kinovea/Kinovea) provides a dedicated native review environment. The current official desktop application targets Windows x86-64. Exais keeps it separate and preserves its KVA files without copying or embedding Kinovea's GPL implementation.

On Windows, set `EXAIS_KINOVEA_EXE` to the full local path of your installed `Kinovea.exe` before starting Exais. The review panel enables handoff when the configured executable exists. Exais copies the source into a per-clip review workspace, preserving the original, and starts Kinovea using literal arguments.

Save annotations in Kinovea, then import the `.kva` file through the clip's review panel. KVA imports are limited to 2 MiB and checked for a known safe XML envelope. Their bytes are preserved. This does **not** establish that the annotation geometry matches the selected source or that a native round trip has been qualified. An existing native working sidecar is not overwritten on subsequent launches; version reconciliation is a later ticket. KVA storage is usable without native Kinovea on the machine.

### football-cv: explicit, reviewable suggestions

The adapter is based on inspection of [andrewneuwirth/football-cv](https://github.com/andrewneuwirth/football-cv) at commit `38f6f758d3e78af3b700f144214c2d4566459c5d`. The upstream pipeline needs its own dependencies, calibration, appropriate footage, and processing. Exais currently imports a bundle shaped like this:

```json
{
  "provider": "football-cv",
  "schema_version": 1,
  "source_sha256": "REPLACE_WITH_THE_SELECTED_CLIPS_64_CHARACTER_SOURCE_SHA256",
  "upstream_revision": "38f6f758d3e78af3b700f144214c2d4566459c5d",
  "positions": {"1": "LB", "2": "QB", "3": "WR"},
  "notes": "Operator-prepared import; review against the source footage."
}
```

Supported broad labels are `DL`, `LB`, `CB`, `S`, `OL`, `QB`, `RB`, and `WR`. Keys are **tracking IDs within this source clip**, not jersey numbers or player identities. The adapter rejects a wrong source hash or unsupported schema. Accepting a suggestion records your review decision; it never silently changes roster assignments or technique tags. A supplied upstream commit is provenance from the operator, not proof that the model executed.

The command builder in `integrations/football_cv.py` can prepare an explicit external runner invocation for developers. The app does not execute that command. [Upstream research](docs/UPSTREAM_RESEARCH.md) documents calibration, detector/documentation drift, the upstream renderer's audio limitation, and licensing boundaries.

## Configuration and operating limits

| Setting | Default / behavior |
| --- | --- |
| `EXAIS_FOOTBALL_DATA` | `~/.exais-football`; overridden by `serve --data-dir` |
| `EXAIS_FOOTBALL_MAX_UPLOAD_BYTES` | 2 GiB per file |
| `EXAIS_KINOVEA_EXE` | Unset; native handoff disabled |
| Export worker | One running job, at most 20 pending/running jobs |
| KVA upload | At most 2 MiB; recognized 2.0/2.1 XML envelope |
| Catalog listing | Paginated API, at most 200 clips per response |
| Source deletion | No source-delete API; archive clips to hide them from normal browsing |

If the application stops with unfinished exports, those jobs become failed with an interruption reason at the next startup. Queue a new export when ready. A successful export has been probed and fully decoded; codec availability is still checked on the computer doing the work.

For a manual backup, stop Exais and copy the **entire data directory**, including the catalog, original sources, sidecars, and exports. Copying only the SQLite file is not a media backup. Automated verified backup/restore and migration recovery have dedicated tickets.

This release is intended for one local user. Remote hosting, authentication, shared editing, and network-mounted multi-process catalogs are outside its current operating model. Media parsing uses the locally installed FFmpeg binaries; keep those binaries maintained. Full desktop installers, macOS/Windows qualification, advanced frame accuracy, and real model evaluation remain in the [roadmap](docs/ROADMAP.md).

## Development and verification

The repository includes `uv.lock` for a reproducible developer environment:

```bash
uv sync --locked --extra dev
uv run ruff check .
uv run ruff format --check src tests
uv run pytest -q
uv run python docs/tickets/validate.py
uv run python -m build
```

Without uv, install with `python -m pip install -e '.[dev]'`; this follows the declared version ranges rather than the exact uv lock. Tests generate their own video and audio in temporary directories. No game film, model weights, runtime database, or credentials are committed. Media tests skip if FFmpeg/ffprobe are missing; a skip is not a passed media gate.

The installed wheel includes the static application. GitHub Actions runs lint, catalog validation, API/real-media tests, and package checks. Optional browser smoke instructions are in the UI guide. Native Kinovea and model execution are tracked separately from automated adapter tests.

## Project map

| Area | Purpose |
| --- | --- |
| [`src/exais_football`](src/exais_football) | Local API, persistence, jobs, media operations, integration adapters |
| [`src/exais_football/static`](src/exais_football/static) | Browser review interface with no Node build step |
| [`tests`](tests) | Generated-media and application regression tests |
| [Product specification](docs/PRODUCT_SPEC.md) | User workflow, scope, and acceptance boundaries |
| [Architecture](docs/ARCHITECTURE.md) | Data model, transactions, media provenance, and adapters |
| [Ticket catalog](docs/tickets/README.md) | All 48 issue mappings, dependencies, and acceptance checks |
| [Decisions](docs/DECISIONS.md) | Why this first implementation uses these boundaries |
| [Qualification](docs/QUALIFICATION.md) | Executed checks and remaining gates |

## License

Exais retains the repository's original [Apache License 2.0](LICENSE). Third-party projects and executables retain their own licenses. No Kinovea source, football-cv source, detector weights, or real football footage is vendored here. See [third-party notices](THIRD_PARTY_NOTICES.md).
