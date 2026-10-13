"""Source-PTS overlays with immutable decisions, optional identities, and retained audio."""

from __future__ import annotations

import shutil
from uuid import uuid4

from .cv_store import CVStore
from .frames import verified_source
from .media import _local_input_options, _parse_probe, _probe_document, _verify_export
from .service import CatalogError, now
from .workspace import register_artifact, run_bounded, workspace_lock


def _stamp(seconds):
    hundredths = max(0, round(seconds * 100))
    return (
        f"{hundredths // 360000}:{hundredths // 6000 % 60:02}:"
        f"{hundredths // 100 % 60:02}.{hundredths % 100:02}"
    )


def safe_ass_text(text):
    # Literal user text must not become ASS override tags or escape sequences.
    return " ".join(str(text).replace("\\", "∖").replace("{", "｛").replace("}", "｝").split())[
        :160
    ]


def labels_at(
    track, decisions, at_seconds, include_unreviewed=False, include_identities=False, roster=None
):
    if track["suppressed"]:
        return None
    label = track["position"] if include_unreviewed else None
    reviewed = False
    identity = None
    for d in decisions:
        if d["track_id"] != track["track_id"] or d["stale"] or d["kind"] == "continuity":
            continue
        p = d["payload"]
        if p["start_seconds"] <= at_seconds < p["end_seconds"]:
            if d["kind"] == "position":
                label, reviewed = p["value"], True
            elif d["kind"] == "identity":
                identity = p["player_id"]
    if label is None or label == "suppressed":
        return None
    display = label + ("" if reviewed else " ?")
    if include_identities and identity is not None:
        display += " · " + (roster or {}).get(identity, "identity unavailable")
    return {"text": display, "reviewed": reviewed, "identity_id": identity}


def freeze_overlay(catalog, run_id, body):
    store = CVStore(catalog)
    view = store.projection(run_id)
    if view["review_version"] != body.expected_review_revision:
        raise CatalogError(
            "version_conflict", "Review decisions changed; reload before exporting", 409
        )
    clip, _ = verified_source(catalog, view["clip_id"])
    end = body.end_seconds if body.end_seconds is not None else clip["source_duration"]
    if not 0 <= body.start_seconds < end <= clip["source_duration"]:
        raise CatalogError("overlay_range", "Choose a range inside the source video", 422)
    if len(view["timestamps"]) > 3000 or any(
        b - a < 1 / 61 for a, b in zip(view["timestamps"], view["timestamps"][1:], strict=False)
    ):
        raise CatalogError(
            "overlay_timing", "This overlay profile supports at most 60 frames/second", 422
        )
    roster = {p["id"]: p["name"] for p in catalog.list_entities("players")}
    with catalog.db.connection() as db:
        current = db.execute("SELECT review_version FROM cv_runs WHERE id=?", (run_id,)).fetchone()[
            0
        ]
        if current != view["review_version"]:
            raise CatalogError(
                "version_conflict", "Decisions changed while preparing the export", 409
            )
    return {
        "schema_version": 1,
        "run_id": run_id,
        "clip": clip,
        "projection": view,
        "model": store.get(run_id)["manifest"]["model"],
        "roster": roster if body.include_identities else {},
        **body.model_dump(),
        "end_seconds": end,
        "requested_at": now(),
        "annotation_timing_tolerance_seconds": 0.01,
    }


def make_ass(snapshot):
    view = snapshot["projection"]
    w, h = view["geometry"]["width"], view["geometry"]["height"]
    font = max(12, min(36, round(h / 28)))
    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {w}",
        f"PlayResY: {h}",
        "WrapStyle: 2",
        "[V4+ Styles]",
        (
            "Format: Name, Fontname, Fontsize, PrimaryColour, "
            "SecondaryColour, OutlineColour, BackColour, Bold, "
            "Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, "
            "Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
            "MarginR, MarginV, Encoding"
        ),
        f"Style: Base,sans-serif,{font},&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,"
        "0,0,0,0,100,100,0,0,1,1,0,7,0,0,0,1",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    count = 0
    for track in view["tracks"]:
        for f in track["frames"]:
            i = f["i"]
            at = view["timestamps"][i]
            after = (
                view["timestamps"][i + 1]
                if i + 1 < len(view["timestamps"])
                else snapshot["clip"]["source_duration"]
            )
            if after <= snapshot["start_seconds"] or at >= snapshot["end_seconds"]:
                continue
            label = labels_at(
                track,
                view["decisions"],
                at,
                snapshot["include_unreviewed"],
                snapshot["include_identities"],
                snapshot["roster"],
            )
            if label is None:
                continue
            color = "&H60F0A0&" if label["reviewed"] else "&H40C8FF&"
            x1, y1, x2, y2 = [round(v) for v in f["box"]]
            # Four thin polygons form the outline, without obscuring the player.
            t = max(1, min(4, round(w / 640)))
            polygon = (
                f"m {x1} {y1} l {x2} {y1} {x2} {y1 + t} {x1} {y1 + t} "
                f"m {x1} {y2 - t} l {x2} {y2 - t} {x2} {y2} {x1} {y2} "
                f"m {x1} {y1} l {x1 + t} {y1} {x1 + t} {y2} {x1} {y2} "
                f"m {x2 - t} {y1} l {x2} {y1} {x2} {y2} {x2 - t} {y2}"
            )
            span = f"{_stamp(at)},{_stamp(after)}"
            lines.append(
                f"Dialogue: 0,{span},Base,,0,0,0,,"
                f"{{\\an7\\pos(0,0)\\bord0\\shad0\\1c{color}\\p1}}{polygon}{{\\p0}}"
            )
            lines.append(
                f"Dialogue: 1,{span},Base,,0,0,0,,{{\\an7\\pos({x1},{max(0, y1 - font - 3)})}}"
                f"{safe_ass_text(label['text'])}"
            )
            count += 1
            if count > 100000:
                raise CatalogError("overlay_limits", "Overlay exceeds 100,000 frame labels", 422)
    return "\n".join(lines) + "\n", count


def render_overlay(catalog, snapshot, cancel, stage):
    clip = snapshot["clip"]
    current, source = verified_source(catalog, clip["id"])
    if current["source_sha256"] != clip["source_sha256"]:
        raise CatalogError("source_mismatch", "The frozen original source changed", 409)
    tool = shutil.which("ffmpeg")
    if not tool:
        raise CatalogError("tool_missing", "Install FFmpeg with its subtitles/libass filter", 503)
    root = catalog.settings.path(f"staging/overlay-{uuid4()}")
    root.mkdir()
    try:
        stage("preparing_reviewed_annotations")
        script, count = make_ass(snapshot)
        (root / "labels.ass").write_text(script, encoding="utf-8")
        temporary = root / "overlay.mp4"
        duration = snapshot["end_seconds"] - snapshot["start_seconds"]
        stage("rendering_audio_preserving_overlay")
        run_bounded(
            [
                tool,
                "-nostdin",
                "-v",
                "error",
                "-filter_threads",
                "1",
                "-threads",
                "2",
                *_local_input_options(),
                "-i",
                str(source),
                "-ss",
                str(snapshot["start_seconds"]),
                "-t",
                str(duration),
                "-map",
                "0:v:0",
                "-map",
                "0:a?",
                "-vf",
                "subtitles=labels.ass,pad=ceil(iw/2)*2:ceil(ih/2)*2",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "20",
                "-pix_fmt",
                "yuv420p",
                "-fps_mode",
                "vfr",
                "-threads",
                "2",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-movflags",
                "+faststart",
                "-map_metadata",
                "-1",
                "-map_chapters",
                "-1",
                str(temporary),
            ],
            cwd=root,
            timeout=300,
            cancel=cancel,
            quota_dir=root,
        )
        stage("verifying_overlay_audio_and_timeline")
        source_info = _parse_probe(_probe_document(source))
        verification = _verify_export(temporary, source_info, duration)
        verified_source(catalog, clip["id"])
        destination = catalog.settings.path(f"artifacts/{uuid4()}.mp4")
        with workspace_lock(catalog):
            temporary.replace(destination)
            try:
                result = register_artifact(
                    catalog,
                    "cv_overlay",
                    clip["id"],
                    destination,
                    {
                        "run_id": snapshot["run_id"],
                        "source_sha256": clip["source_sha256"],
                        "clip_id": clip["id"],
                        "source_asset_id": clip["source_asset_id"],
                        "start_seconds": snapshot["start_seconds"],
                        "end_seconds": snapshot["end_seconds"],
                        "review_revision": snapshot["projection"]["review_version"],
                        "continuity_revision": snapshot["projection"]["continuity_version"],
                        "include_unreviewed": snapshot["include_unreviewed"],
                        "include_identities": snapshot["include_identities"],
                        "decisions": snapshot["projection"]["decisions"],
                        "model": snapshot["model"],
                        "rendered_frame_labels": count,
                        "verification": verification,
                        "annotation_timing_tolerance_seconds": 0.01,
                        "timeline": "source_relative_pts; output zero is start_seconds",
                    },
                )
                if (
                    CVStore(catalog).get(snapshot["run_id"])["review_version"]
                    != snapshot["projection"]["review_version"]
                ):
                    with catalog.db.connection(write=True) as db:
                        db.execute(
                            "UPDATE artifact_records SET stale=1 WHERE id=?", (result["id"],)
                        )
                    result["stale"] = True
                return result
            except BaseException:
                destination.unlink(missing_ok=True)
                raise
    finally:
        shutil.rmtree(root, ignore_errors=True)
