# Third-party boundaries

The Exais source in this repository is covered by the existing Apache License 2.0 in `LICENSE`. The original repository license file is preserved unchanged.

## Runtime dependencies

Python dependencies are declared in `pyproject.toml` and resolved in `uv.lock`. They are installed separately and retain the licenses in their distributions. FastAPI, Starlette, Uvicorn, Pydantic, and python-multipart are not copied into this source tree. Development dependencies are similarly separate.

FFmpeg and ffprobe are invoked as operator-installed command-line executables. Their build options and included codecs affect both capabilities and applicable licensing; Exais does not distribute their binaries. Review the [FFmpeg legal page](https://ffmpeg.org/legal.html) for any distribution you prepare.

## Optional external review and analysis

- **Kinovea** — [upstream](https://github.com/Kinovea/Kinovea), GPL v2. Exais observes its documented command-line/file interfaces, validates a small known XML envelope, and stores operator-provided sidecar bytes. No Kinovea implementation is embedded. A prepared local handoff does not constitute native-platform qualification.
- **football-cv** — [reviewed source](https://github.com/andrewneuwirth/football-cv/tree/38f6f758d3e78af3b700f144214c2d4566459c5d), MIT repository license. Exais defines its own source-bound result envelope and an optional external command builder; it neither vendors the toolkit nor imports its heavy inference dependencies at startup. Detector implementations, optional extras, model weights, and upstream datasets may have different terms. The toolkit's repository license alone does not establish permission for every downstream model or use.

The project does not infer permission from a public repository when no license is identified. Other film-review prototypes considered in research remain references only. Any later incorporation of code, model weights, or media must record its actual provenance and applicable permission in the corresponding ticket.
