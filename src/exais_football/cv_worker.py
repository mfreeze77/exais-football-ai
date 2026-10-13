"""External, deliberately launched worker; this module is never imported by the web app.

Uses pinned football-cv stages, with local-only safe weight construction replacing
its stale detector-extra guard. The external checkout is not modified or vendored.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import platform
import socket
import sys
from pathlib import Path


def no_network(*args, **kwargs):
    raise RuntimeError(
        "CV workers are offline. Acquire and checksum weights deliberately before running."
    )


def configure(config):
    socket.create_connection = no_network
    socket.socket.connect = no_network
    socket.socket.connect_ex = no_network
    sys.path.insert(0, config["repository"])
    import torch

    torch.hub.download_url_to_file = no_network
    torch.hub.load_state_dict_from_url = no_network
    torch.set_num_threads(2)
    device = config["device"]
    if device == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("Configured CUDA device unavailable")
    if device == "mps" and not torch.backends.mps.is_available():
        raise RuntimeError("Configured MPS device unavailable")
    import cv2

    cv2.setNumThreads(2)
    return torch


def probe(config):
    torch = configure(config)
    packages = ["torch", "torchvision", "numpy", "opencv-python", "scipy", "rfdetr", "supervision"]
    versions = {}
    for package in packages:
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = None
    import footballcv.cli
    import footballcv.detect

    if config["backend"] == "rfdetr-nano":
        import rfdetr

        if not hasattr(rfdetr, "RFDETRNano"):
            raise RuntimeError("RFDETRNano is not available")
    else:
        pass
    return {
        "python": platform.python_version(),
        "system": platform.system(),
        "machine": platform.machine(),
        "packages": versions,
        "device": config["device"],
        "cuda_version": torch.version.cuda,
        "stage_order": list(footballcv.cli.ORDER),
        "detection_extra_guard": (
            "replaced only in isolated worker after explicit backend "
            "imports; no Ultralytics code used"
        ),
        "downloads": "disabled",
        "weight_loader": "torch.load(weights_only=True) before model construction",
    }


def execute(request, output):
    config = request["configuration"]
    torch = configure(config)
    from footballcv import api, detect

    weight = Path(config["weights"])
    digest = hashlib.sha256()
    with weight.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != config["weights_sha256"]:
        raise RuntimeError("Weight checksum changed")
    state = torch.load(weight, map_location="cpu", weights_only=True)
    if not isinstance(state, dict):
        raise RuntimeError("Weights must be a safe tensor-state mapping")

    # The guarded external detector preserves upstream detect()/run() behavior.
    # It avoids both default network downloads and the unrelated Ultralytics guard.
    class LocalDetector(detect.PersonDetector):
        def __init__(self, conf=0.3, imgsz=1920, backend=None):
            self.conf = float(conf)
            self.imgsz = config["imgsz"]
            self.device = config["device"]
            self._torch = torch
            self.backend = "rfdetr" if config["backend"] == "rfdetr-nano" else "torchvision"
            if self.backend == "rfdetr":
                import rfdetr

                self._model = rfdetr.RFDETRNano(pretrain_weights=str(weight), device=self.device)
                self.model_name = "rfdetr-nano-local"
            else:
                import torchvision

                model = torchvision.models.detection.fasterrcnn_resnet50_fpn_v2(
                    weights=None, weights_backbone=None
                )
                tensors = state.get("model", state.get("state_dict", state))
                model.load_state_dict(tensors, strict=True)
                model.eval()
                self._model = model.to(self.device)
                self.model_name = "torchvision-fasterrcnn-resnet50-fpn-v2-local"

    detect.PersonDetector = LocalDetector
    output.mkdir(parents=True, exist_ok=True)
    api.analyze(
        video=request["video"],
        calibration=request["calibration"],
        snap=request["snap_frame"],
        data_dir=output,
        play_id=request["play_id"],
    )


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--probe")
    p.add_argument("--request", type=Path)
    p.add_argument("--output", type=Path)
    args = p.parse_args()
    if args.probe:
        # Imports may emit library notices; keep the JSON protocol on stdout only.
        import contextlib

        with contextlib.redirect_stdout(sys.stderr):
            result = probe(json.loads(args.probe))
        print(json.dumps(result, allow_nan=False))
        return
    if not args.request or not args.output:
        p.error("Provide a request and output directory")
    execute(json.loads(args.request.read_text()), args.output)


if __name__ == "__main__":
    main()
