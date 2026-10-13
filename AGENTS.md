# EXAIS Football AI engineering rules

Build a local film library for the owner's existing American-football clips. Preserve original media; trimming and overlays are derived artifacts. Keep human labels distinct from model suggestions, and never treat per-play tracking IDs as roster identities.

- The first delivery is a working Python/FastAPI + SQLite application with a same-origin browser UI; no frontend build service is required.
- Bind to loopback. Do not add remote access, uploads to a provider, model downloads, or external publishing as side effects of starting the app.
- Follow `docs/IMPLEMENTATION_CONTRACT.md` for the API and ownership boundaries. API changes must update the UI and tests together.
- Keep all runtime footage, databases, exports, logs, and local configuration out of git. Use generated footage in tests; never commit the user's film.
- Every media or AI artifact retains its original clip ID, source SHA-256, and source-relative timing. Edited video must retain source audio when present.
- Kinovea remains an external Windows application. football-cv remains an optional external toolkit, with its revision and limitations recorded. Do not copy GPL/AGPL sources into this Apache-2.0 repository.
- All subprocesses use argument arrays, `shell=False`, bounded execution and checked return codes. Request fields must never choose executable paths or arbitrary filesystem paths.
- Tickets live in `docs/tickets/`, with a machine-readable catalog and GitHub issue mappings. Separate implemented, locally verified, and unqualified capabilities.
- Run the relevant tests, formatter/linter, package checks, and real FFmpeg workflow before reporting a milestone. Record real-model and Windows checks as unrun when unavailable.
- Make changes on the implementation branch and deliver a pull request. Do not merge without the owner's authorization.
