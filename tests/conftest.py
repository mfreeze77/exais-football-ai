"""Generated media and isolated catalogs for application-level qualification."""

from __future__ import annotations

import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient

from exais_football.app import create_app
from exais_football.config import Settings


@pytest.fixture(scope="session")
def sample_video(tmp_path_factory):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("Install FFmpeg and ffprobe for application media qualification")
    path = tmp_path_factory.mktemp("application-fixture") / "practice.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-hide_banner",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=320x180:rate=25:duration=3",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000:duration=3",
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "libx264",
            "-threads",
            "2",
            "-c:a",
            "aac",
            "-pix_fmt",
            "yuv420p",
            "-shortest",
            str(path),
        ],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=True,
        timeout=30,
    )
    return path


@pytest.fixture
def client(tmp_path):
    app = create_app(Settings(data_dir=tmp_path / "film-room"))
    with TestClient(app, base_url="http://127.0.0.1") as local_client:
        yield local_client


@pytest.fixture
def import_clip(sample_video):
    def perform(client, **fields):
        with sample_video.open("rb") as stream:
            response = client.post(
                "/api/imports",
                files={"file": (sample_video.name, stream, "video/mp4")},
                data=fields,
            )
        assert response.status_code == 201, response.text
        return response.json()["clip"]

    return perform
