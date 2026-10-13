"""Selected, frozen film packages: UTF-8 JSON/CSV and optional audio-preserving cuts."""

from __future__ import annotations

import csv
import io
import json
import shutil
import zipfile
from uuid import uuid4

from .media import export_trim
from .recovery import _cancel, _stream_copy, inspect_package
from .service import CatalogError, encode, file_sha256, now
from .workspace import managed_path, register_artifact, workspace_lock


def csv_safe(value: str) -> str:
    # Quote dangerous prefixes even after leading whitespace; original Unicode is
    # retained losslessly in metadata.json alongside the spreadsheet-safe CSV.
    return "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) else value


def freeze_selection(
    catalog, selected: list[dict], include_media: bool = False, include_sidecars: bool = True
) -> dict:
    if not 1 <= len(selected) <= 500 or len({s["clip_id"] for s in selected}) != len(selected):
        raise CatalogError(
            "package_selection", "Select 1–500 distinct clips in the intended order", 422
        )
    with catalog.db.connection() as db:
        clips = []
        games = {}
        players = {}
        for item in selected:
            clip = catalog._clip(db, item["clip_id"])
            if clip["version"] != item["version"]:
                raise CatalogError(
                    "version_conflict",
                    "A selected clip changed. Refresh the package selection",
                    409,
                )
            asset = catalog._require(db, "clips", clip["id"])["asset_id"]
            source = db.execute("SELECT path FROM assets WHERE id=?", (asset,)).fetchone()[0]
            notes = [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM notes WHERE clip_id=? ORDER BY created_at,id", (clip["id"],)
                )
            ]
            sidecar = db.execute("SELECT * FROM sidecars WHERE clip_id=?", (clip["id"],)).fetchone()
            suggestions = [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM analyses WHERE clip_id=? ORDER BY created_at,id", (clip["id"],)
                )
            ]
            if clip["game_id"]:
                games[clip["game_id"]] = dict(catalog._require(db, "games", clip["game_id"]))
            for player_id in clip["player_ids"]:
                players[player_id] = dict(catalog._require(db, "players", player_id))
            drawings = [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM drawing_revisions WHERE clip_id=? ORDER BY revision",
                    (clip["id"],),
                )
            ]
            cv_runs = [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM cv_runs WHERE clip_id=? ORDER BY created_at,id", (clip["id"],)
                )
            ]
            for run in cv_runs:
                run["manifest"] = json.loads(run["manifest"])
                run["decisions"] = [
                    dict(r)
                    for r in db.execute(
                        "SELECT * FROM cv_decisions WHERE run_id=? ORDER BY revision", (run["id"],)
                    )
                ]
                for decision in run["decisions"]:
                    decision["payload"] = json.loads(decision["payload"])
                    player_id = decision["payload"].get("player_id")
                    if player_id:
                        players[player_id] = dict(catalog._require(db, "players", player_id))
                # Raw files live in a complete CV bundle, never absolute paths.
                run.pop("directory", None)
            clips.append(
                {
                    "clip": clip,
                    "source_path": source,
                    "notes": notes,
                    "sidecar": dict(sidecar) if sidecar and include_sidecars else None,
                    "position_suggestions": suggestions,
                    "drawing_revisions": drawings,
                    "cv_runs": cv_runs,
                }
            )
    return {
        "schema_version": 1,
        "created_at": now(),
        "include_media": include_media,
        "include_sidecars": include_sidecars,
        "clips": clips,
        "games": list(games.values()),
        "players": list(players.values()),
    }


def create_package(catalog, snapshot: dict, cancel=None) -> dict:
    package_id = str(uuid4())
    root = catalog.settings.path(f"staging/package-{package_id}")
    root.mkdir(mode=0o700)
    destination = catalog.settings.path(f"packages/{package_id}.zip")
    temporary = destination.with_name(destination.name + ".partial")
    with workspace_lock(catalog):
        try:
            public = {
                "schema_version": 1,
                "created_at": snapshot["created_at"],
                "games": snapshot["games"],
                "players": snapshot["players"],
                "clips": [],
            }
            files = []
            order = []
            for index, item in enumerate(snapshot["clips"]):
                _cancel(cancel)
                clip = item["clip"]
                order.append(clip["id"])
                public["clips"].append(
                    {k: v for k, v in item.items() if k not in {"source_path", "sidecar"}}
                )
                prefix = f"clips/{index:04d}-{clip['id']}"
                if item["sidecar"]:
                    sidecar = item["sidecar"]
                    path = managed_path(catalog.settings.data_dir, sidecar["path"])
                    if file_sha256(path) != sidecar["sha256"]:
                        raise CatalogError(
                            "sidecar_changed",
                            "A selected sidecar changed after package preparation",
                            409,
                        )
                    files.append((prefix + ".kva", path, "sidecar", sidecar["sha256"]))
                if snapshot["include_media"]:
                    source = managed_path(catalog.settings.data_dir, item["source_path"])
                    if file_sha256(source) != clip["source_sha256"]:
                        raise CatalogError(
                            "source_changed",
                            "A selected source changed after package preparation",
                            409,
                        )
                    rendered = root / (clip["id"] + ".mp4")
                    result = export_trim(
                        source, rendered, clip["start_seconds"], clip["end_seconds"]
                    )
                    if result["source_sha256"] != clip["source_sha256"]:
                        raise CatalogError(
                            "source_changed", "A selected source changed during export", 409
                        )
                    files.append(
                        (prefix + ".mp4", rendered, "trimmed_media", file_sha256(rendered))
                    )
                    public["clips"][-1]["export"] = result
            metadata = root / "metadata.json"
            metadata.write_text(encode(public), encoding="utf-8")
            files.append(("metadata.json", metadata, "metadata", file_sha256(metadata)))
            text = io.StringIO(newline="")
            writer = csv.writer(text)
            writer.writerow(
                [
                    "order",
                    "clip_id",
                    "source_asset_id",
                    "source_sha256",
                    "title",
                    "game_id",
                    "start_seconds",
                    "end_seconds",
                    "label_revision",
                    "tags_json",
                    "players_json",
                    "notes_json",
                ]
            )
            for index, item in enumerate(snapshot["clips"]):
                c = item["clip"]
                writer.writerow(
                    [
                        index,
                        c["id"],
                        c["source_asset_id"],
                        c["source_sha256"],
                        csv_safe(c["title"]),
                        c["game_id"] or "",
                        c["start_seconds"],
                        c["end_seconds"],
                        c["version"],
                        json.dumps(c["tags"], ensure_ascii=False),
                        json.dumps(c["player_ids"]),
                        json.dumps(item["notes"], ensure_ascii=False),
                    ]
                )
            csv_path = root / "clips.csv"
            csv_path.write_text(text.getvalue(), encoding="utf-8-sig", newline="")
            files.append(
                ("clips.csv", csv_path, "spreadsheet_safe_metadata", file_sha256(csv_path))
            )
            manifest = {
                "schema_version": 1,
                "kind": "exais-selected-film",
                "created_at": now(),
                "package_id": package_id,
                "clip_order": order,
                "files": [],
                "metadata_only": not snapshot["include_media"],
                "policy": (
                    "Only selected clips and their related records; "
                    "no original source video unless explicitly "
                    "provided as a selected full-range cut. KVA "
                    "remains editable metadata, not burned-in text."
                ),
            }
            with zipfile.ZipFile(
                temporary, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=1
            ) as archive:
                for name, path, kind, expected in files:
                    size = path.stat().st_size
                    with (
                        path.open("rb") as source,
                        archive.open(name, "w", force_zip64=True) as target,
                    ):
                        digest = _stream_copy(source, target, expected_size=size, cancel=cancel)
                    if digest != expected:
                        raise CatalogError(
                            "package_file_changed", "A package member changed while copying", 409
                        )
                    manifest["files"].append(
                        {"path": name, "kind": kind, "size": size, "sha256": digest}
                    )
                archive.writestr("manifest.json", encode(manifest))
            _cancel(cancel)
            inspect_package(temporary, expected_kind="exais-selected-film", cancel=cancel)
            temporary.replace(destination)
            try:
                return register_artifact(catalog, "portable_package", None, destination, manifest)
            except BaseException:
                destination.unlink(missing_ok=True)
                raise
        finally:
            temporary.unlink(missing_ok=True)
            shutil.rmtree(root)
