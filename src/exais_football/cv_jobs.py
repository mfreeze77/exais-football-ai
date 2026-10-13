"""Frozen, offline optional inference with isolated copies and validated publication."""

from __future__ import annotations

import hashlib
import shutil
import time
import zipfile
from pathlib import Path
from uuid import uuid4

from . import cv_environment
from .calibration import Calibration
from .cv_models import Preflight
from .cv_store import BUNDLE_KIND, REQUIRED_FILES, CVStore
from .frames import frame_index, verified_source
from .service import CatalogError, encode, file_sha256, now
from .study import token
from .workspace import atomic_bytes, managed_path, run_bounded


def freeze_inference(catalog, body, clip_id):
    intent = Preflight.model_validate(body.model_dump(exclude={"environment_fingerprint"}))
    preflight = Calibration(catalog).preflight(clip_id, intent)
    if preflight["status"] != "ready":
        raise CatalogError("cv_needs_input", "; ".join(preflight["reasons"]), 422)
    environment = cv_environment.probe(catalog)
    if environment["fingerprint"] != body.environment_fingerprint:
        raise CatalogError(
            "cv_environment_changed", "Check the environment again before running", 409
        )
    clip = catalog.get_clip(clip_id)
    if clip["width"] * clip["height"] > 3840 * 2160:
        raise CatalogError(
            "cv_geometry_limit", "Use a source no larger than 4K for this profile", 422
        )
    return {
        "schema_version": 1,
        "clip": clip,
        "preflight": preflight,
        "environment": environment,
        "requested_at": now(),
        "limits": {"seconds": 900, "frames": 3000, "threads": 2, "disk_bytes": 2 * 1024**3},
    }


def bundle_outputs(directory, destination, manifest):
    """Package the allowlisted raw stage outputs; missing files are never synthesized."""
    files = []
    with zipfile.ZipFile(destination, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(REQUIRED_FILES):
            path = managed_path(directory, name)
            size = path.stat().st_size
            if size > 16 * 1024**2:
                raise CatalogError("analysis_limits", "A stage artifact exceeds 16 MiB", 422)
            raw = path.read_bytes()
            files.append({"path": name, "size": size, "sha256": hashlib.sha256(raw).hexdigest()})
            archive.writestr(name, raw)
        manifest = {**manifest, "files": files}
        archive.writestr("manifest.json", encode(manifest))
    return manifest


def infer(catalog, snapshot, cancel, stage):
    started = time.monotonic()
    clip = snapshot["clip"]
    stage("verifying_frozen_environment")
    env = cv_environment.probe(catalog)
    if env["fingerprint"] != snapshot["environment"]["fingerprint"]:
        raise CatalogError(
            "cv_environment_changed", "The frozen environment changed; submit a new job", 409
        )
    current, source = verified_source(catalog, clip["id"])
    if current["source_sha256"] != clip["source_sha256"]:
        raise CatalogError("source_mismatch", "The frozen source no longer matches", 409)
    configuration = cv_environment.configuration(catalog)
    root = catalog.settings.path(f"staging/cv-{uuid4()}")
    root.mkdir(mode=0o700)
    try:
        stage("preparing_verified_copy")
        staged = root / "source.mp4"
        # Never give an external toolkit access to the catalog's original file as input.
        with source.open("rb") as src, staged.open("xb") as dst:
            while chunk := src.read(1024**2):
                if cancel.is_set():
                    raise CatalogError("job_cancelled", "Analysis was cancelled", 409)
                dst.write(chunk)
        if file_sha256(staged) != clip["source_sha256"]:
            raise CatalogError(
                "source_changed", "The staged analysis copy failed verification", 409
            )
        play_id = str(uuid4())
        request = {
            "configuration": configuration,
            "video": str(staged),
            "calibration": snapshot["preflight"]["calibration"]["payload"],
            "snap_frame": snapshot["preflight"]["intent"]["snap_frame"],
            "play_id": play_id,
        }
        request_file = root / "request.json"
        atomic_bytes(request_file, encode(request).encode())
        output = root / "output"
        stage("external_detection_tracking_and_positions")
        log = run_bounded(
            [
                configuration["python"],
                str(Path(__file__).with_name("cv_worker.py")),
                "--request",
                str(request_file),
                "--output",
                str(output),
            ],
            cwd=root,
            timeout=snapshot["limits"]["seconds"],
            cancel=cancel,
            env=cv_environment.offline_environment(),
            max_output=8 * 1024**2,
            quota_dir=root,
            quota_bytes=snapshot["limits"]["disk_bytes"],
        )
        stage("validating_complete_pipeline")
        verified_source(catalog, clip["id"])
        if file_sha256(staged) != clip["source_sha256"]:
            raise CatalogError(
                "analysis_copy_changed", "The external toolkit modified its input copy", 409
            )
        index = frame_index(catalog, clip["id"])
        manifest = {
            "schema_version": 1,
            "kind": BUNDLE_KIND,
            "clip_id": clip["id"],
            "clip_version": clip["version"],
            "source_asset_id": clip["source_asset_id"],
            "source_sha256": clip["source_sha256"],
            "timeline": {
                "kind": "source_relative_pts",
                "frame_count": index["count"],
                "pts_sha256": token(index["timestamps"]),
            },
            "geometry": {"width": clip["width"], "height": clip["height"]},
            "transform_chain": [{"kind": "identity", "source_sha256": clip["source_sha256"]}],
            "snap_frame": request["snap_frame"],
            "model": env,
            "preflight": snapshot["preflight"],
            "limits": snapshot["limits"],
            "runtime_seconds": time.monotonic() - started,
            "log_sha256": hashlib.sha256(log).hexdigest(),
            "origin": "explicit_external_inference",
            "created_at": now(),
            "accuracy_qualified": False,
        }
        archive = root / "bundle.zip"
        bundle_outputs(output / "plays" / play_id, archive, manifest)
        stage("publishing_verified_analysis")
        return CVStore(catalog).ingest(clip["id"], archive)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def register_cv(jobs):
    from .overlay import render_overlay

    jobs.register("cv_analysis", lambda s, c, stage: infer(jobs.catalog, s, c, stage))
    jobs.register("cv_overlay", lambda s, c, stage: render_overlay(jobs.catalog, s, c, stage))
