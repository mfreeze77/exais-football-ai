"""Explicit operator-owned CV environment. Health checks never execute or download models."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from .service import CatalogError, encode, file_sha256
from .study import token
from .workspace import run_bounded

UPSTREAM = "38f6f758d3e78af3b700f144214c2d4566459c5d"
CONFIG_NAME = "cv-environment.json"


def configuration(catalog):
    path = catalog.settings.data_dir / CONFIG_NAME
    if not path.is_file():
        return None
    if path.is_symlink() or path.stat().st_size > 16384:
        raise CatalogError("cv_configuration", "Invalid local CV configuration", 422)
    try:
        data = json.loads(path.read_text())
        if set(data) != {
            "schema_version",
            "python",
            "repository",
            "revision",
            "backend",
            "device",
            "weights",
            "weights_sha256",
            "weights_license",
            "weights_source",
            "imgsz",
        }:
            raise ValueError("fields")
        if data["schema_version"] != 1 or data["revision"] != UPSTREAM:
            raise ValueError("revision")
        if data["backend"] not in {"torchvision", "rfdetr-nano"} or data["device"] not in {
            "cpu",
            "cuda",
            "mps",
        }:
            raise ValueError("backend/device")
        if type(data["imgsz"]) is not int or not 320 <= data["imgsz"] <= 1280:
            raise ValueError("imgsz")
        if not re.fullmatch("[a-f0-9]{64}", data["weights_sha256"]):
            raise ValueError("checksum")
        if not data["weights_license"].strip() or not data["weights_source"].startswith("https://"):
            raise ValueError("license/source")
        for key in ("python", "repository", "weights"):
            p = Path(data[key]).expanduser()
            if not p.is_absolute() or not p.exists() or str(p).startswith(("//", "\\\\")):
                raise ValueError("local paths")
        if (
            not Path(data["repository"]).is_dir()
            or not Path(data["weights"]).is_file()
            or Path(data["weights"]).stat().st_size > 2 * 1024**3
        ):
            raise ValueError("weights")
        return data
    except (ValueError, KeyError, TypeError) as exc:
        raise CatalogError(
            "cv_configuration",
            "Review the local CV configuration, pinned revision, and explicit weight provenance",
            422,
        ) from exc


def capability(catalog):
    try:
        config = configuration(catalog)
    except CatalogError as exc:
        return {"available": False, "status": "invalid_configuration", "reason": exc.message}
    if not config:
        return {
            "available": False,
            "status": "not_configured",
            "reason": (
                "Configure an isolated Python environment and locally "
                "acquired weights. No automatic downloads occur."
            ),
        }
    return {
        "available": False,
        "configured": True,
        "status": "explicit_probe_required",
        "backend": config["backend"],
        "device": config["device"],
        "reason": (
            "Run the deliberate environment check before submitting "
            "analysis. Configuration is not inference qualification."
        ),
    }


def probe(catalog):
    config = configuration(catalog)
    if not config:
        raise CatalogError(
            "cv_not_configured",
            "Configure the isolated CV environment with the command-line guide",
            503,
        )
    root = Path(config["repository"])
    scratch = catalog.settings.path("staging")
    head = (
        run_bounded(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            cwd=scratch,
            timeout=15,
            max_output=1024,
        )
        .decode()
        .strip()
    )
    dirty = (
        run_bounded(
            ["git", "-C", str(root), "status", "--porcelain", "--untracked-files=no"],
            cwd=scratch,
            timeout=15,
            max_output=65536,
        )
        .decode()
        .strip()
    )
    if head != UPSTREAM or dirty:
        raise CatalogError(
            "cv_upstream_changed",
            "The CV checkout must match the pinned revision with no tracked changes",
            409,
        )
    if file_sha256(Path(config["weights"])) != config["weights_sha256"]:
        raise CatalogError("cv_weight_changed", "Model weights failed the configured checksum", 409)
    worker = Path(__file__).with_name("cv_worker.py")
    output = run_bounded(
        [config["python"], str(worker), "--probe", encode(config)],
        cwd=scratch,
        timeout=90,
        max_output=131072,
        env=offline_environment(),
    )
    try:
        details = json.loads(output)
    except ValueError as exc:
        raise CatalogError(
            "cv_probe_failed",
            "External environment check did not return its structured manifest",
            503,
        ) from exc
    manifest = {
        "configuration_fingerprint": token(config),
        "repository_revision": head,
        "backend": config["backend"],
        "device": config["device"],
        "weights_sha256": config["weights_sha256"],
        "weights_license": config["weights_license"],
        "weights_source": config["weights_source"],
        "imgsz": config["imgsz"],
        "environment": details,
        "profile": "source-native-frames-v1",
        "inference_qualified": False,
    }
    return {**manifest, "fingerprint": token(manifest), "status": "ready_for_explicit_run"}


def offline_environment():
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in {"PYTHONPATH", "PYTHONHOME", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"}
    }
    env.update(
        {
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "HF_DATASETS_OFFLINE": "1",
            "WANDB_MODE": "disabled",
            "OMP_NUM_THREADS": "2",
            "MKL_NUM_THREADS": "2",
            "OPENBLAS_NUM_THREADS": "2",
        }
    )
    return env
