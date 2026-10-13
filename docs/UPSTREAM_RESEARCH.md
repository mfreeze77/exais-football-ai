# Upstream research and reuse boundaries

Reviewed on **2026-10-13 UTC**. Repository inspection used primary upstream files, and Kinovea feature/platform findings used the project's official manual and versioned release notes. This was source/documentation review; it was not a real model run or a native Windows installation test.

The application is built around the owner's existing clips. Kinovea supplies external detailed review, while football-cv is a candidate optional analysis toolkit. The first implementation establishes safe handoff/import contracts; advanced interoperability is separately ticketed.

## Kinovea

### What is supported by primary documentation

| Finding | Primary evidence | Consequence for this project |
| --- | --- | --- |
| Motion observation, video comparison, annotations, and measurement are core functions. | [Official reference manual](https://kinovea.org/help/en/index.html) | Keep an external detailed-review option instead of rebuilding every tool before the library is useful. |
| The current download requirements specify Windows x86-64 and exclude Apple Silicon support. | [Official downloads](https://kinovea.org/download/) | Browser library support on macOS is separate from Kinovea support. |
| Kinovea is released under GPL v2. | [Official license section](https://kinovea.org/download/#license) | Keep the application external; no GPL source is copied into this Apache-2.0 repository. |
| Working-zone bounds and import settings can be saved with annotations; imported annotation times can depend on time origins. | [2023.1 release notes](https://kinovea.org/download/releases/2023.1.html) | KVA offers a useful persistence target, but source/time-origin mapping needs explicit tests. |
| The second-video command-line option opens a comparison window. | [2025.1 release notes](https://kinovea.org/download/releases/2025.1.html) | Safe comparison launch can be built and qualified for named versions. |
| Later releases include timing and annotation-loading fixes. | [2025.2 release notes](https://kinovea.org/download/releases/2025.2.html) | Pin supported versions and retain release-specific KVA fixtures. |

The linked manual identifies itself as version 0.9.5. It is useful background but must not override later release notes. The inspected download page linked 2025.2.0 at review time; that is an observation of the page, not a promise that this project's native adapter has been tested on it.

### The accepted integration boundary

M1 may launch a locally configured Windows executable and preserve a validated KVA file associated with a clip. Its XML validation rejects unsafe constructs and preserves original bytes. It does not translate every annotation or synchronize library metadata.

EFA-026–028 add recognized-field inspection, reviewable proposed changes, conflict reconciliation, and comparison launch. EFA-041 supplies the real Windows acceptance. A persisted working-zone field in an upstream format does not establish that this application currently imports it.

Kinovea source is not vendored, embedded, or rewritten from its GPL implementation. Project-owned adapters exchange files and arguments with the separately installed application. Any future redistribution or deeper code integration requires a fresh documented dependency/license decision.

## football-cv

**Inspected code revision:** `38f6f758d3e78af3b700f144214c2d4566459c5d`

**Repository:** [andrewneuwirth/football-cv](https://github.com/andrewneuwirth/football-cv)

**Upstream source license:** [MIT license at the inspected revision](https://github.com/andrewneuwirth/football-cv/blob/38f6f758d3e78af3b700f144214c2d4566459c5d/LICENSE). This describes that source; detector dependencies, serialized model artifacts, and downloaded weights need their own inventory and terms.

### Verified code surfaces

| Surface | What was inspected | Integration use |
| --- | --- | --- |
| [footballcv/api.py](https://github.com/andrewneuwirth/football-cv/blob/38f6f758d3e78af3b700f144214c2d4566459c5d/footballcv/api.py) | High-level analyze, calibration/snap helpers, and position/label loading. | Supply stable play IDs, persistent data directories, and reviewed inputs. |
| [footballcv/detect.py](https://github.com/andrewneuwirth/football-cv/blob/38f6f758d3e78af3b700f144214c2d4566459c5d/footballcv/detect.py) | Detector selection, dependency guard, device choice, letterbox transforms, native-pixel output. | Record actual backend/device and qualify dependencies instead of relying on README wording. |
| [viewer/backend/overlay.py](https://github.com/andrewneuwirth/football-cv/blob/38f6f758d3e78af3b700f144214c2d4566459c5d/viewer/backend/overlay.py) | Position/track box rendering, OpenCV frame writing, and FFmpeg transcoding. | Add project-owned provenance, source audio, bounded execution, and timestamp qualification. |
| [viewer/backend/serve.py](https://github.com/andrewneuwirth/football-cv/blob/38f6f758d3e78af3b700f144214c2d4566459c5d/viewer/backend/serve.py) | Development viewer endpoints for frames, calibration, analysis, and artifacts. | Reference the artifact workflow; do not adopt it as the library server. |
| [viewer/web/index.html](https://github.com/andrewneuwirth/football-cv/blob/38f6f758d3e78af3b700f144214c2d4566459c5d/viewer/web/index.html) | Landing-page upload handler attaches a local object URL for playback. | Do not mistake a working file picker or sample diagram for running analysis. |
| [pyproject.toml](https://github.com/andrewneuwirth/football-cv/blob/38f6f758d3e78af3b700f144214c2d4566459c5d/pyproject.toml) | Python package, optional detection/ML extras, and packaged model artifacts. | Isolate the toolkit and explicitly inventory the resolved environment. |

### Specific limitations found

**Detector documentation drift.** The README and detection extra describe YOLO/Ultralytics. The inspected detector chooses RF-DETR when available and otherwise torchvision Faster R-CNN. Its dependency guard still checks for torch and ultralytics. Its device helper selects MPS when available and otherwise CPU. Installation success alone therefore does not establish the backend or GPU behavior. EFA-029 resolves this with an explicit environment and actual capability report. [Detector implementation](https://github.com/andrewneuwirth/football-cv/blob/38f6f758d3e78af3b700f144214c2d4566459c5d/footballcv/detect.py), [dependency declarations](https://github.com/andrewneuwirth/football-cv/blob/38f6f758d3e78af3b700f144214c2d4566459c5d/pyproject.toml).

**Default analysis storage is temporary.** The high-level API creates a temporary directory when none is provided and uses a generic default play ID. A durable film library must pass both explicitly and retain the per-run inputs/intermediates. EFA-032 and EFA-033 own that boundary. [Analysis API](https://github.com/andrewneuwirth/football-cv/blob/38f6f758d3e78af3b700f144214c2d4566459c5d/footballcv/api.py).

**The overlay loses source audio.** The renderer decodes/writes video frames and transcodes that newly created video; it does not map audio from the source clip. It also reconstructs video using the reported frame rate. Audio retention and timing on varied footage require an explicit project adapter and real-media checks. EFA-034 owns the complete behavior. [Overlay renderer](https://github.com/andrewneuwirth/football-cv/blob/38f6f758d3e78af3b700f144214c2d4566459c5d/viewer/backend/overlay.py).

**The landing upload is playback-only.** The inspected index page expressly distinguishes local playback from the analysis backend. Its animated sample field and counts are not evidence of inference on an uploaded clip. This application must never present sample players as results from the owner's film. [Landing page](https://github.com/andrewneuwirth/football-cv/blob/38f6f758d3e78af3b700f144214c2d4566459c5d/viewer/web/index.html).

### Author-reported capability versus project qualification

The upstream README describes alignment-based groups DL/LB/CB/S and OL/QB/RB/WR, manual calibration with at least four field correspondences, and same-game calibration transfer. It reports approximately 70% overall accuracy on 21 hand-labeled plays containing 162 players; those labels are private. That is the author's benchmark, not this project's reproduced result or a promised result on the owner's footage. [Pinned README](https://github.com/andrewneuwirth/football-cv/blob/38f6f758d3e78af3b700f144214c2d4566459c5d/README.md).

The code-review evidence supports testing a reusable toolkit. It does not establish automatic named-player identity, reliable play/route recognition, or coaching judgments. Track IDs belong to a particular run/play. Accepting a position label does not prove which roster player it is.

M1 therefore accepts source-bound suggestion JSON and records explicit review state. Model execution, calibration, correction overlays, roster mapping, and real-film quality are assigned to EFA-029–039. EFA-037 measures representative owner-approved film with transparent class counts, uncertainty, failure cases, and separated evaluation data.

## Reuse policy

- Reuse the external tools' documented interfaces where useful; preserve the inspected revision and the original source link.
- Keep the base library lightweight and independent of detector installation.
- Do not copy unlicensed code or GPL/AGPL implementations from previously mentioned alternatives into this repository.
- Do not infer dependency or model-weight permissions from the application repository's license.
- When an upstream change alters an artifact schema, detector behavior, or KVA mapping, update adapter fixtures and qualification before changing a supported claim.
- Keep raw input/output records separate from project validation, human review, and derived media.

The machine-readable source registry lives inside [tickets/catalog.json](tickets/catalog.json). It is intentionally a set of provenance pointers, not a bundled copy of upstream repositories. EFA-047 maintains future pin and dependency changes.
