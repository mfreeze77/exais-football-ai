"""Run with the wheel's Python from outside the checkout to verify installation.

Uses only the standard library as a test client. Starts an isolated local
application, checks the packaged UI modules and health, and cleans up afterward.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

import exais_football


def main() -> None:
    location = Path(exais_football.__file__).resolve()
    if "site-packages" not in location.parts:
        raise SystemExit(f"Expected an installed wheel, but loaded {location}")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix="exais-wheel-") as temporary:
        environment = dict(os.environ)
        environment.pop("PYTHONPATH", None)
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "exais_football.cli",
                "serve",
                "--data-dir",
                str(Path(temporary) / "film"),
                "--port",
                str(port),
            ],
            cwd=temporary,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        try:
            origin = f"http://127.0.0.1:{port}"
            deadline = time.monotonic() + 20
            while True:
                try:
                    with urllib.request.urlopen(origin + "/api/health", timeout=1) as response:
                        health = json.load(response)
                    break
                except (urllib.error.URLError, TimeoutError):
                    if time.monotonic() >= deadline or process.poll() is not None:
                        raise RuntimeError("Installed application did not become healthy") from None
                    time.sleep(0.1)
            assert health["status"] == "ok" and health["version"] == exais_football.__version__
            assets = {
                "/": "text/html",
                "/static/styles.css": "text/css",
                "/static/app.js": "javascript",
                "/static/review.js": "javascript",
                "/static/api.js": "javascript",
                "/static/ui.js": "javascript",
            }
            for route, media_type in assets.items():
                with urllib.request.urlopen(origin + route, timeout=5) as response:
                    assert response.status == 200
                    assert media_type in response.headers["Content-Type"]
                    assert len(response.read()) > 100
            print(f"PASS installed wheel {exais_football.__version__}: health and all UI assets")
        finally:
            process.terminate()
            try:
                _, errors = process.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                _, errors = process.communicate(timeout=5)
            if process.returncode not in {0, -15}:
                print(errors.decode("utf-8", errors="replace")[-4000:], file=sys.stderr)


if __name__ == "__main__":
    main()
