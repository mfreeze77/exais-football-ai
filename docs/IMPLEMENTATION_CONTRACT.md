# First milestone implementation contract

## Goal and ownership

A local application for importing existing video clips, non-destructive in/out selection, football metadata, timed notes, favorites, playlists, exports, and durable review records. Python 3.11+, FastAPI, SQLite, FFmpeg/ffprobe. Browser JS has no remote runtime dependencies.

Root owns `src/exais_football/{app,config,db,models,service,jobs,cli}.py`, packaging and API/integration tests. Media agent owns `media.py`, `integrations/`, and tests for those modules. UI agent owns `static/` and its UI guide. Roadmap agent owns `docs/tickets/`, product/architecture/research docs; root owns this file and final qualification evidence.

## API conventions

Prefix `/api`. Errors: `{ "error": { "code": "machine_code", "message": "Human-readable message" } }`. Lists of games/players/playlists are arrays. Clip list is `{items: Clip[], total: number}`. All times are seconds relative to the original uploaded file, never relative to an exported edit. IDs are opaque UUID strings. A source SHA-256 identifies bytes, not a person. All core endpoints are local-only. No delete-source endpoint.

### Clip object

`id, title, game_id (nullable), phase (offense|defense|special_teams|other), review_status (new|review|approved|needs_work), favorite (bool), archived (bool), description, start_seconds, end_seconds, duration (selected interval), source_duration, source_sha256, original_filename, width, height, fps, has_audio, tags (string[]), player_ids (string[]), version (int), created_at, updated_at, source_url, thumbnail_url`.

### Core routes

- `GET /health`: `{status, version, capabilities:{ffmpeg, ffprobe, kinovea, football_cv}}`; capability values have `available` and optional `reason`.
- `GET /stats`: `{clips, games, players, favorites, total_seconds}` (active clips).
- `GET /games`, `POST /games {title, opponent, date, season, notes}`. Returns game (`id` plus fields). Date/season/notes default empty; title and opponent required.
- `GET /players`, `POST /players {name, jersey, position}`. Returns player; jersey/position default empty.
- `POST /imports` multipart `file`, optional `title`, optional `game_id`. Returns `{clip, duplicate:bool}`. Duplicate source returns its existing clip; no silent overwrite.
- `GET /clips?q=&game_id=&player_id=&tag=&phase=&review_status=&favorite=&archived=false&limit=100&offset=0`.
- `GET /clips/{id}`. `PATCH /clips/{id}` accepts any editable Clip fields plus REQUIRED current `version`; stale edits return 409. `tags` and `player_ids` replace those lists. Immutable media fields cannot be changed. Trim must satisfy `0 <= start < end <= source_duration`.
- `GET /clips/{id}/source`, `GET /clips/{id}/thumbnail`. Source supports byte ranges/HEAD; thumbnail is optional and may return 404 if unavailable.
- `GET /clips/{id}/notes`, `POST /clips/{id}/notes {body, at_seconds}`. Note: `{id,clip_id,body,at_seconds,created_at}`. Timestamp is optional; otherwise must be within source duration. `DELETE /clips/{id}/notes/{note_id}` removes only that note.
- `GET /clips/{id}/history`: revision snapshots newest first. `POST /clips/{id}/restore {version, current_version}` restores editable metadata from a previous version and creates a new revision.
- `POST /clips/{id}/exports` body `{}`: enqueue audio-preserving accurate trim from a frozen snapshot. Returns Job.
- `GET /jobs`, `GET /jobs/{id}`, `GET /jobs/{id}/file`. Job: `{id,clip_id,status:queued|running|complete|failed,kind,created_at,updated_at,error,download_url,clip_version}`. On restart, interrupted jobs become failed; do not pretend they completed. UI polls pending exports only.
- `GET /tags`: `[{name,count}]`.
- `GET /playlists`, `POST /playlists {title,description}`. `GET /playlists/{id}` returns `{id,title,description,clip_ids,clips}`. `PUT /playlists/{id}/clips {clip_ids:[...]}` saves the whole ordered list; validates IDs/duplicates. No source copies.

### Integration routes (initial scope)

- `GET /clips/{id}/kinovea`: capability and handoff description; `POST /clips/{id}/kinovea/launch {}` launches only a configured local executable on Windows. No arbitrary command/executable in requests.
- `POST /clips/{id}/kinovea/annotations` multipart `file`: preserve a validated `.kva` sidecar, size bounded; `GET` downloads it. No claim of native two-way tag sync.
- `POST /clips/{id}/analysis` accepts imported football-cv suggestions with `source_sha256`, `upstream_revision`, `positions` (mapping of per-clip tracking ID to DL/LB/CB/S/OL/QB/RB/WR), and `notes` optional. Reject mismatched sources, malformed IDs/labels, non-finite data and unknown provider schema. Returns immutable suggestion record with `status:unreviewed`.
- `GET /clips/{id}/analysis` returns records. `PATCH /clips/{id}/analysis/{analysis_id} {status:accepted|rejected|unreviewed}` records human review; does not silently add roster identity or play/technique tags.

## Media module contract

`MediaError(code, message)` with attributes `.code/.message`.

`probe_video(path: Path) -> dict` keys `duration,width,height,fps,has_audio`; validate finite positive duration and dimensions. `generate_thumbnail(source: Path, destination: Path, at_seconds:float=0) -> Path`. `export_trim(source:Path,destination:Path,start_seconds:float,end_seconds:float) -> dict` reports probe of output, H.264 MP4, optional source audio mapped and encoded AAC, accurate trim, atomic replacement only after verification. `media_capabilities() -> dict` ffmpeg/ffprobe capability entries. Use PATH-resolved executables, command arrays, timeouts, `-nostdin`, local file-only processing, bounded resources.

## Integration module contract

`integrations/kinovea.py`: `capability(executable: str|None) -> dict`; `validate_kva(content:bytes) -> dict` (safe XML, no DTD/entities, root/schema checks, preserve original bytes), `launch(executable:str,video:Path) -> None` Windows only, no shell. Root handles workspace copying and path allowlisting.

`integrations/football_cv.py`: `validate_positions(payload:dict,source_sha256:str) -> dict` validates imported suggestion bundle; `build_run_command(python_executable:Path, video:Path, calibration:Path, data_dir:Path, play_id:str) -> list[str]` for optional external toolkit runner if feasible. Actual heavy model execution is not part of M1 acceptance. Record upstream pinned revision and license boundaries in docs. Ensure persistent play IDs/directory when preparing runner; do not claim automatic named roster identity.

## UI

Deliver a polished local film-room UI: clip grid/list, search + filters, game/player management, batch uploads with progress, useful empty states, video review with looping between in/out, mark-in/mark-out, editable title/tags/phase/review/player, favorites/archive, timed notes, export status/download, and playlist viewing. Keep keyboard shortcuts inactive while editing text. Preserve unsaved changes and avoid racey clip-selection responses. Use `textContent` or DOM APIs for user-provided strings. No demo content presented as the user's film. Review UI should state the AI import feature is advanced and model execution requires external setup.

## Testing and delivery

Use synthetic FFmpeg video with an audio tone to verify import, range playback, persistence, trims, real export duration/audio and immutable source hash. Verify duplicate import, optimistic concurrency, invalid relationships, path traversal/unsafe uploads, wrong-source AI results and note timestamps. Preserve meaningful failures in jobs. Build/install wheel in an isolated environment and prove static assets are included. Automated UI smoke in a local headless browser if available; otherwise report that gate unrun. Test fixtures are generated into temporary directories. No real film, model weights, credentials or database enters git.
