"""Reviewed KVA 2.1 field import. File-format interoperability, not GPL code reuse."""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from uuid import uuid4

from .frames import frame_index, verified_source
from .integrations.kinovea import UPSTREAM_REVISION, validate_kva
from .models import ClipPatch
from .service import CatalogError, encode, file_sha256, now
from .study import token
from .workspace import managed_path, workspace_lock


class KVAReview:
    def __init__(self, catalog):
        self.catalog = catalog

    def _incoming(self, clip_id: str, source_confirmed: bool):
        clip, source = verified_source(self.catalog, clip_id)
        sidecar = self.catalog.sidecar(clip_id)
        if sidecar is None:
            raise CatalogError("kva_missing", "Upload a KVA sidecar first", 404)
        raw = managed_path(self.catalog.settings.data_dir, sidecar["path"]).read_bytes()
        summary = validate_kva(raw)
        if summary["sha256"] != sidecar["sha256"]:
            raise CatalogError("sidecar_changed", "The preserved KVA checksum changed", 409)
        if not source_confirmed:
            raise CatalogError(
                "kva_source_confirmation",
                "Confirm this KVA describes this preserved video before interpreting its fields",
                422,
            )
        if summary["format_version"] != "2.1":
            raise CatalogError(
                "kva_fields_unsupported",
                (
                    "This version is preserved as raw XML; only "
                    "inspected KVA 2.1 fields can be proposed"
                ),
                422,
            )
        root = ET.fromstring(raw)
        # Direct fields must be unique. Never interpret external paths or silently
        # select a conflicting duplicate element.
        names = [node.tag for node in root]
        if len(names) != len(set(names)):
            raise CatalogError(
                "kva_ambiguous", "Duplicate top-level KVA fields need manual review", 422
            )

        def text(name, default=""):
            return root.findtext(name, default=default)

        producer = text("Producer")
        if not re.fullmatch(r"Kinovea\.2025\.[12](?:\.[0-9]+)*", producer):
            raise CatalogError(
                "kva_producer_unqualified",
                "Preserved, but this producer is outside the inspected 2025.1/2025.2 field profile",
                422,
            )
        if text("CaptureRecording").casefold() == "true":
            raise CatalogError(
                "kva_capture_unsupported",
                "Capture-recording time origins require manual review",
                422,
            )
        expected_names = {
            Path(clip["original_filename"]).stem,
            clip_id,
            f"{clip_id}-left",
            f"{clip_id}-right",
        }
        # The single-player handoff from milestone one is named clip.mp4.
        # Accept that stem only when its independently verified managed copy exists.
        native = self.catalog.settings.path(f"reviews/{clip_id}/native/clip{source.suffix}")
        if native.is_file() and file_sha256(native) == clip["source_sha256"]:
            expected_names.add("clip")
        original = text("OriginalFilename")
        if original not in expected_names:
            raise CatalogError(
                "kva_wrong_source",
                "KVA filename does not match this source or managed review copy",
                409,
            )
        if text("ByteLength") and text("ByteLength") != str(source.stat().st_size):
            raise CatalogError(
                "kva_wrong_source", "KVA file length differs from the preserved source", 409
            )
        index = frame_index(self.catalog, clip_id, create=True)
        ticks = index["raw_timestamps"]
        positions = {t: i for i, t in enumerate(ticks)}
        try:
            if int(text("FirstTimeStamp")) != ticks[0]:
                raise ValueError("first timestamp")

            def mapped(value, end=False):
                tick = int(value)
                if tick not in positions:
                    raise ValueError("timestamp not on source frame")
                n = positions[tick] + int(end)
                return index["timestamps"][n] if n < len(ticks) else clip["source_duration"]

            start = mapped(text("SelectionStart"))
            end = mapped(text("SelectionEnd"), True)
            if not 0 <= start < end <= clip["source_duration"]:
                raise ValueError("selection")
            title = text("GlobalTitle").strip()
            if len(title) > 240:
                raise ValueError("title too long")
            keyframes = {}
            incoming_frames = root.findall("./Keyframes/Keyframe")
            if len(incoming_frames) > 1000:
                raise ValueError("too many keyframes")
            for keyframe in incoming_frames:
                if any(
                    len(keyframe.findall(name)) > 1 for name in ("Timestamp", "Name", "Comment")
                ):
                    raise ValueError("duplicate keyframe field")
                key = keyframe.get("id")
                if not key or key in keyframes:
                    raise ValueError("missing/duplicate keyframe id")
                name = keyframe.findtext("Name", default="").strip()
                comment = keyframe.findtext("Comment", default="").strip()
                body = " — ".join(t for t in (name, comment) if t)
                if not body:
                    body = "Kinovea keyframe"
                if len(body) > 10000:
                    raise ValueError("comment too long")
                keyframes[key] = {
                    "body": body,
                    "at_seconds": mapped(keyframe.findtext("Timestamp")),
                }
        except (ValueError, TypeError, KeyError) as exc:
            raise CatalogError(
                "kva_time_ambiguous",
                (
                    "The KVA selection/keyframes do not map exactly "
                    "to this source presentation clock. Raw XML is "
                    "preserved; no approximate times were imported."
                ),
                422,
            ) from exc
        supported = {
            "FormatVersion",
            "Producer",
            "OriginalFilename",
            "FullPath",
            "ByteLength",
            "ImageSize",
            "GlobalTitle",
            "FirstTimeStamp",
            "SelectionStart",
            "SelectionEnd",
            "Keyframes",
            "AverageTimeStampsPerFrame",
            "VideoFramerate",
            "CaptureFramerate",
            "TimeOrigin",
        }
        return {
            "title": title or None,
            "range": {"start_seconds": start, "end_seconds": end},
            "notes": keyframes,
            "sidecar_sha256": sidecar["sha256"],
            "source_sha256": clip["source_sha256"],
            "profile": "Kinovea-2025.x-KVA-2.1-exact-native-PTS",
            "upstream_revision": UPSTREAM_REVISION,
            "unsupported_preserved": [n for n in names if n not in supported]
            + (["Keyframes/Drawings"] if root.findall("./Keyframes/Keyframe/Drawings") else []),
            "timing": (
                "Exact native source PTS; SelectionEnd is inclusive "
                "and advances to the next frame boundary. TimeOrigin "
                "remains display metadata, not a timeline shift."
            ),
        }

    def _plan(self, db, clip_id, incoming):
        clip = self.catalog._clip(db, clip_id)
        current_sidecar = db.execute(
            "SELECT sha256 FROM sidecars WHERE clip_id=?", (clip_id,)
        ).fetchone()
        if not current_sidecar or current_sidecar[0] != incoming["sidecar_sha256"]:
            raise CatalogError("preview_stale", "KVA changed; build a new preview", 409)
        last = db.execute(
            (
                "SELECT payload FROM kva_imports WHERE clip_id=? AND "
                "status='accepted' ORDER BY created_at DESC,id DESC LIMIT 1"
            ),
            (clip_id,),
        ).fetchone()
        previous = json.loads(last[0]) if last else {}
        baseline = previous.get("incoming", {})
        note_map = previous.get("note_map", {})
        fields = []

        def field(key, current, value, old, applied):
            if value is None:
                return
            fields.append(
                {
                    "key": key,
                    "baseline": old,
                    "current": current,
                    "incoming": value,
                    "changed": current != value,
                    "recommended": "catalog" if previous and value == old else "kva",
                    "local_changed": bool(previous and current != applied),
                    "conflict": bool(
                        previous and old != value and current != applied and current != value
                    ),
                }
            )

        field(
            "title",
            clip["title"],
            incoming["title"],
            baseline.get("title"),
            previous.get("applied_fields", {}).get("title"),
        )
        current_range = {k: clip[k] for k in ("start_seconds", "end_seconds")}
        field(
            "range",
            current_range,
            incoming["range"],
            baseline.get("range"),
            previous.get("applied_fields", {}).get("range"),
        )
        for key, value in incoming["notes"].items():
            stored = note_map.get(key)
            row = (
                db.execute(
                    "SELECT body,at_seconds FROM notes WHERE id=? AND clip_id=?",
                    (stored["note_id"], clip_id),
                ).fetchone()
                if stored
                else None
            )
            current = dict(row) if row else None
            field(
                "note:" + key,
                current,
                value,
                baseline.get("notes", {}).get(key),
                stored.get("applied") if stored else None,
            )
        data = {
            "clip_id": clip_id,
            "clip_version": clip["version"],
            "incoming": incoming,
            "fields": fields,
            "note_map": note_map,
            "baseline_token": token(previous),
            "source_sha256": clip["source_sha256"],
        }
        return {**data, "preview_token": token(data)}

    def preview(self, clip_id, source_confirmed):
        incoming = self._incoming(clip_id, source_confirmed)
        with self.catalog.db.connection() as db:
            return self._plan(db, clip_id, incoming)

    def apply(self, clip_id, body):
        incoming = self._incoming(clip_id, body.source_confirmed)
        with workspace_lock(self.catalog), self.catalog.db.connection(write=True) as db:
            plan = self._plan(db, clip_id, incoming)
            if plan["preview_token"] != body.preview_token:
                raise CatalogError(
                    "preview_stale",
                    "Catalog or KVA changed; review a fresh three-way comparison",
                    409,
                )
            keys = {f["key"] for f in plan["fields"]}
            if set(body.choices) != keys:
                raise CatalogError(
                    "kva_choices", "Choose keep-catalog or use-KVA for every proposed field", 422
                )
            if body.decision == "rejected":
                db.execute(
                    "INSERT INTO kva_imports VALUES(?,?,?,?,?,?)",
                    (
                        str(uuid4()),
                        clip_id,
                        incoming["sidecar_sha256"],
                        encode(plan),
                        "rejected",
                        now(),
                    ),
                )
                return {"status": "rejected", "changed": False}
            changes = {}
            mapping = dict(plan["note_map"])
            applied_fields = {}
            notes_changed = 0
            for item in plan["fields"]:
                key = item["key"]
                use = body.choices[key] == "kva"
                value = item["incoming"] if use else item["current"]
                if key in {"title", "range"}:
                    applied_fields[key] = value
                    if use and item["changed"]:
                        changes.update({"title": value} if key == "title" else value)
                elif use and item["changed"]:
                    external_id = key[5:]
                    stored = mapping.get(external_id)
                    note_id = stored["note_id"] if stored else str(uuid4())
                    db.execute(
                        (
                            "INSERT INTO "
                            "notes(id,clip_id,body,at_seconds,created_"
                            "at) VALUES(?,?,?,?,?) ON CONFLICT(id) DO "
                            "UPDATE SET body=excluded.body,at_seconds=excluded.at_seconds"
                        ),
                        (note_id, clip_id, value["body"], value["at_seconds"], now()),
                    )
                    mapping[external_id] = {"note_id": note_id, "applied": value}
                    notes_changed += 1
            if changes:
                self.catalog._patch(db, clip_id, ClipPatch(version=plan["clip_version"], **changes))
            last = db.execute(
                (
                    "SELECT sidecar_sha256,payload FROM kva_imports "
                    "WHERE clip_id=? AND status='accepted' ORDER BY "
                    "created_at DESC,id DESC LIMIT 1"
                ),
                (clip_id,),
            ).fetchone()
            if last and last[0] == incoming["sidecar_sha256"] and not changes and not notes_changed:
                return {"status": "already_applied", "changed": False}
            record = {
                **plan,
                "choices": body.choices,
                "applied_fields": applied_fields,
                "note_map": mapping,
            }
            db.execute(
                "INSERT INTO kva_imports VALUES(?,?,?,?,?,?)",
                (
                    str(uuid4()),
                    clip_id,
                    incoming["sidecar_sha256"],
                    encode(record),
                    "accepted",
                    now(),
                ),
            )
            return {
                "status": "accepted",
                "changed": bool(changes or notes_changed),
                "notes_changed": notes_changed,
                "clip": self.catalog._clip(db, clip_id),
            }

    def history(self, clip_id):
        with self.catalog.db.connection() as db:
            self.catalog._require(db, "clips", clip_id)
            return [
                {
                    "id": r["id"],
                    "status": r["status"],
                    "sidecar_sha256": r["sidecar_sha256"],
                    "created_at": r["created_at"],
                    "payload": json.loads(r["payload"]),
                }
                for r in db.execute(
                    "SELECT * FROM kva_imports WHERE clip_id=? ORDER BY created_at DESC LIMIT 50",
                    (clip_id,),
                )
            ]
