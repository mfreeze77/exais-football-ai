"""Real generated-film checks; synthetic footage never enters the repository."""

from __future__ import annotations

import array
import hashlib
import math
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from exais_football import media
from exais_football.media import MediaError


def run_ffmpeg(*arguments: str) -> bytes:
    executable = shutil.which("ffmpeg")
    if not executable or not shutil.which("ffprobe"):
        pytest.skip("Real FFmpeg/ffprobe workflow requires installed local executables.")
    return subprocess.run(
        [
            executable,
            "-nostdin",
            "-hide_banner",
            "-v",
            "error",
            "-y",
            "-filter_threads",
            "1",
            *arguments,
        ],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=True,
        timeout=30,
    ).stdout


@pytest.fixture(scope="module")
def film(tmp_path_factory) -> Path:
    destination = tmp_path_factory.mktemp("real-football-fixture") / "red then blue.mp4"
    run_ffmpeg(
        "-f",
        "lavfi",
        "-i",
        "color=c=red:s=160x90:r=25:d=1",
        "-f",
        "lavfi",
        "-i",
        "color=c=blue:s=160x90:r=25:d=1",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=880:sample_rate=48000:duration=2",
        "-filter_complex",
        "[0:v][1:v]concat=n=2:v=1:a=0[v]",
        "-map",
        "[v]",
        "-map",
        "2:a:0",
        "-c:v",
        "libx264",
        "-g",
        "50",
        "-threads",
        "2",
        "-c:a",
        "aac",
        "-pix_fmt",
        "yuv420p",
        str(destination),
    )
    return destination


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_probe_real_video_and_capabilities(film):
    probe = media.probe_video(film)
    assert probe == {"duration": 2.0, "width": 160, "height": 90, "fps": 25.0, "has_audio": True}
    states = media.media_capabilities()
    assert states["ffmpeg"]["available"] and states["ffprobe"]["available"]
    assert states["ffmpeg"]["verification"] == "executable_only"


def test_real_accurate_trim_preserves_tone_and_source_bytes(film, tmp_path):
    before = digest(film)
    destination = tmp_path / "clip tightened.mp4"
    result = media.export_trim(film, destination, 1.12, 1.64)
    assert result["duration"] == pytest.approx(0.52, abs=0.08)
    assert result["has_audio"] is True
    assert result["source_sha256"] == digest(film) == before
    assert result["output_sha256"] == digest(destination)
    assert result["time_origin"] == "source_file"
    assert result["start_seconds"] == 1.12 and result["end_seconds"] == 1.64
    assert result["verification"]["full_decode"] == "passed"
    assert result["verification"]["audio_preservation"] == "verified"

    # One GOP spans the color change: a stream-copy/keyframe cut would retain
    # red. The decoded first output frame must instead show the selected blue.
    rgb = run_ffmpeg(
        "-i",
        str(destination),
        "-map",
        "0:v:0",
        "-frames:v",
        "1",
        "-an",
        "-vf",
        "scale=1:1",
        "-pix_fmt",
        "rgb24",
        "-threads",
        "1",
        "-f",
        "rawvideo",
        "pipe:1",
    )
    assert len(rgb) == 3 and rgb[2] > 180 and rgb[0] < 60
    pcm = run_ffmpeg(
        "-i",
        str(destination),
        "-map",
        "0:a:0",
        "-ac",
        "1",
        "-ar",
        "8000",
        "-c:a",
        "pcm_s16le",
        "-f",
        "s16le",
        "pipe:1",
    )
    samples = array.array("h", pcm)
    if sys.byteorder != "little":
        samples.byteswap()
    assert len(samples) >= 3000
    assert math.sqrt(sum(value * value for value in samples) / len(samples)) > 1000
    crossings = sum(a <= 0 < b for a, b in zip(samples, samples[1:], strict=False))
    measured_frequency = crossings / (len(samples) / 8000)
    assert measured_frequency == pytest.approx(880, abs=50)
    assert not list(tmp_path.glob(".*.mp4"))


def test_real_silent_video_export_and_thumbnail(film, tmp_path):
    silent = tmp_path / "silent.mkv"
    run_ffmpeg("-i", str(film), "-map", "0:v:0", "-c:v", "copy", "-an", str(silent))
    assert media.probe_video(silent)["has_audio"] is False
    exported = media.export_trim(silent, tmp_path / "silent-trim.mp4", 0.2, 0.9)
    assert exported["has_audio"] is False
    assert exported["verification"]["audio_preservation"] == "not_applicable"
    thumbnail = media.generate_thumbnail(silent, tmp_path / "preview.jpg", at_seconds=1.2)
    assert thumbnail.read_bytes().startswith(b"\xff\xd8")
    assert thumbnail.stat().st_size > 100


def test_real_export_preserves_multiple_audio_streams(tmp_path):
    source = tmp_path / "two-tone-tracks.mp4"
    run_ffmpeg(
        "-f",
        "lavfi",
        "-i",
        "color=c=green:s=160x90:r=25:d=1",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:duration=1",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=880:duration=1",
        "-map",
        "0:v",
        "-map",
        "1:a",
        "-map",
        "2:a",
        "-c:v",
        "libx264",
        "-threads",
        "2",
        "-c:a",
        "aac",
        str(source),
    )
    result = media.export_trim(source, tmp_path / "two-tone-trim.mp4", 0.12, 0.8)
    assert result["verification"]["audio_stream_count"] == 2


def test_real_odd_dimensions_are_padded_without_cropping(tmp_path):
    source = tmp_path / "odd-sized.mkv"
    run_ffmpeg(
        "-f",
        "lavfi",
        "-i",
        "testsrc=size=161x91:rate=25:duration=1",
        "-c:v",
        "ffv1",
        "-threads",
        "1",
        str(source),
    )
    assert media.probe_video(source)["width"] == 161
    result = media.export_trim(source, tmp_path / "even-sized.mp4", 0.1, 0.7)
    assert (result["width"], result["height"]) == (162, 92)


def test_real_rotation_metadata_matches_displayed_export_dimensions(film, tmp_path):
    source = tmp_path / "rotated.mp4"
    run_ffmpeg(
        "-display_rotation:v:0",
        "90",
        "-i",
        str(film),
        "-map",
        "0",
        "-c",
        "copy",
        str(source),
    )
    assert media.probe_video(source)["width"] == 90
    result = media.export_trim(source, tmp_path / "rotated-trim.mp4", 0.2, 0.8)
    assert (result["width"], result["height"]) == (90, 160)


@pytest.mark.parametrize(
    "start,end", [(-1, 1), (1, 1), (0, 9), (math.nan, 1), (0, math.inf), (True, 2)]
)
def test_invalid_trim_never_creates_output(film, tmp_path, start, end):
    destination = tmp_path / "invalid.mp4"
    with pytest.raises(MediaError, match="Trim must") as error:
        media.export_trim(film, destination, start, end)
    assert error.value.code == "invalid_trim"
    assert not destination.exists()


def test_source_overwrite_and_hardlink_protected(film, tmp_path):
    before = digest(film)
    with pytest.raises(MediaError) as error:
        media.export_trim(film, film, 0, 1)
    assert error.value.code == "source_overwrite_forbidden"
    link = tmp_path / "hardlink.mp4"
    link.hardlink_to(film)
    with pytest.raises(MediaError) as error:
        media.export_trim(film, link, 0, 1)
    assert error.value.code == "source_overwrite_forbidden"
    assert digest(film) == before


def test_atomic_destination_survives_verification_failure(film, tmp_path, monkeypatch):
    destination = tmp_path / "existing.mp4"
    previous = b"previous artifact must survive a failed new render"
    destination.write_bytes(previous)

    def reject_output(*args):
        raise MediaError(
            "export_audio_missing", "Injected verification failure after real encoding."
        )

    monkeypatch.setattr(media, "_verify_export", reject_output)
    with pytest.raises(MediaError) as error:
        media.export_trim(film, destination, 0.2, 0.8)
    assert error.value.code == "export_audio_missing"
    assert destination.read_bytes() == previous
    assert not list(tmp_path.glob(".*.mp4"))


def test_source_mutation_withholds_publication(film, tmp_path, monkeypatch):
    source = tmp_path / "mutable-copy.mp4"
    shutil.copyfile(film, source)
    destination = tmp_path / "withheld.mp4"
    original_verifier = media._verify_export

    def change_source_after_real_verification(*args):
        result = original_verifier(*args)
        with source.open("ab") as handle:
            handle.write(b"changed")
        return result

    monkeypatch.setattr(media, "_verify_export", change_source_after_real_verification)
    with pytest.raises(MediaError) as error:
        media.export_trim(source, destination, 0.2, 0.8)
    assert error.value.code == "source_changed"
    assert not destination.exists()


def test_real_invalid_media_playlist_and_unwritable_parent(film, tmp_path):
    corrupt = tmp_path / "corrupt.mp4"
    corrupt.write_text("this is not a video")
    with pytest.raises(MediaError) as error:
        media.probe_video(corrupt)
    assert error.value.code == "invalid_media"
    playlist = tmp_path / "external.m3u8"
    playlist.write_text(
        "#EXTM3U\n#EXT-X-VERSION:3\n#EXTINF:10,\nhttps://example.invalid/video.ts\n"
    )
    with pytest.raises(MediaError) as error:
        media.probe_video(playlist)
    assert error.value.code == "invalid_media"
    parent_file = tmp_path / "not-a-directory"
    parent_file.write_bytes(b"keep")
    with pytest.raises(MediaError) as error:
        media.export_trim(film, parent_file / "export.mp4", 0, 1)
    assert error.value.code == "media_io_error"
    assert parent_file.read_bytes() == b"keep"


def test_missing_tool_is_unavailable_not_passed(film, monkeypatch):
    monkeypatch.setattr(media.shutil, "which", lambda _: None)
    states = media.media_capabilities()
    assert not states["ffprobe"]["available"]
    with pytest.raises(MediaError) as error:
        media.probe_video(film)
    assert error.value.code == "ffprobe_missing"


def test_real_process_timeout_is_a_failure():
    with pytest.raises(MediaError) as error:
        media._run(
            [sys.executable, "-c", "import time; time.sleep(2)"],
            timeout=0.05,
            purpose="Timeout test",
        )
    assert error.value.code == "media_timeout"


@pytest.mark.parametrize("duration", ["NaN", "Infinity", "-1", "0", None])
def test_unknown_or_nonfinite_metadata_rejected(duration):
    document = {
        "streams": [{"codec_type": "video", "width": 160, "height": 90, "avg_frame_rate": "25/1"}],
        "format": {"duration": duration},
    }
    with pytest.raises(MediaError) as error:
        media._parse_probe(document)
    assert error.value.code == "invalid_video_duration"
