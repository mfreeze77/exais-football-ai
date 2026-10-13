"""Generated CV fixtures test contracts; they do not establish real-model accuracy."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from uuid import uuid4

import pytest

from exais_football import cv_environment
from exais_football.cv_jobs import bundle_outputs
from exais_football.cv_store import BUNDLE_KIND, CVStore
from exais_football.evaluation import evaluate
from exais_football.frames import frame_index
from exais_football.geometry import homography, project
from exais_football.overlay import labels_at, safe_ass_text
from exais_football.service import CatalogError, file_sha256
from exais_football.study import token
from exais_football.workspace import run_bounded


def request(client, path, body=None, status=200):
    result = client.post("/api/cv" + path, json={} if body is None else body)
    assert result.status_code == status, result.text
    return result.json()


def cal_body(clip):
    return {
        "source_sha256": clip["source_sha256"],
        "expected_revision": 0,
        "frame": 0,
        "points": [
            {"px": 20, "py": 20, "fx": 20, "fy": 0},
            {"px": 300, "py": 20, "fx": 40, "fy": 0},
            {"px": 300, "py": 160, "fx": 40, "fy": 53.333},
            {"px": 20, "py": 160, "fx": 20, "fy": 53.333},
        ],
    }


def save_cal(client, clip):
    body = cal_body(clip)
    prefix = f"/clips/{clip['id']}/calibrations"
    plan = request(client, prefix + "/preview", body)
    return request(client, prefix, {**body, "preview_token": plan["preview_token"]}, 201)


def synthetic_bundle(catalog, clip, root, modify=None):
    index = frame_index(catalog, clip["id"], create=True)
    count = index["count"]
    tracks = [
        {"track_id": 1, "frames": [{"i": i, "box": [35, 45, 90, 150]} for i in range(count)]},
        {"track_id": 2, "frames": [{"i": i, "box": [210, 50, 270, 150]} for i in range(count)]},
    ]
    data = {
        "detections.json": {
            "frames": [
                {"i": i, "boxes": [[35, 45, 90, 150, 0.7], [210, 50, 270, 150, 0.8]]}
                for i in range(count)
            ]
        },
        "tracks.json": {"tracks": tracks},
        "positions.json": {"1": "LB", "2": "WR"},
        "field_coords.json": {
            "homographies": [
                {"i": i, "H": [[1, 0, 0], [0, 1, 0], [0, 0, 1]]} for i in range(count)
            ],
            "tracks": [
                {
                    "track_id": t["track_id"],
                    "frames": [{"i": f["i"], "x": 20, "y": 20} for f in t["frames"]],
                }
                for t in tracks
            ],
        },
        "team_colors.json": {"teams": {"1": "A", "2": "B"}, "flip_events": []},
        "labels.json": {"source": "synthetic_fixture"},
        "calibration.json": {"frame": 0, "points": cal_body(clip)["points"]},
        "snap.json": {"frame": 20},
    }
    manifest = {
        "schema_version": 1,
        "kind": BUNDLE_KIND,
        "clip_id": clip["id"],
        "source_sha256": clip["source_sha256"],
        "source_asset_id": clip["source_asset_id"],
        "timeline": {
            "kind": "source_relative_pts",
            "frame_count": count,
            "pts_sha256": token(index["timestamps"]),
        },
        "geometry": {"width": clip["width"], "height": clip["height"]},
        "transform_chain": [{"kind": "identity", "source_sha256": clip["source_sha256"]}],
        "snap_frame": 20,
        "model": {
            "backend": "synthetic-test-only",
            "device": "fixture",
            "repository_revision": cv_environment.UPSTREAM,
            "weights_sha256": "a" * 64,
            "weights_license": "not-a-model",
        },
        "origin": "synthetic_fixture",
        "accuracy_qualified": False,
    }
    if modify:
        modify(manifest, data)
    directory = root / str(uuid4())
    directory.mkdir()
    for name, value in data.items():
        (directory / name).write_text(json.dumps(value))
    archive = root / (str(uuid4()) + ".zip")
    bundle_outputs(directory, archive, manifest)
    return archive


def import_run(client, clip, tmp_path, modify=None):
    archive = synthetic_bundle(client.app.state.catalog, clip, tmp_path, modify)
    result = client.post(
        f"/api/cv/clips/{clip['id']}/runs",
        files={"file": ("analysis.zip", archive.read_bytes(), "application/zip")},
    )
    assert result.status_code == 201, result.text
    return result.json()


def decision(client, run, kind="position", tid="1", **values):
    body = {
        "expected_revision": run.get("review_version", 0),
        "start_seconds": 0,
        "end_seconds": 3,
        "evidence": "Reviewed synthetic fixture",
        **values,
    }
    if kind == "continuity":
        body.pop("start_seconds")
        body.pop("end_seconds")
    return request(client, f"/runs/{run['id']}/tracks/{tid}/{kind}", body)


def test_optional_cv_absent_has_no_side_effect_and_blocks_run(client, import_clip):
    clip = import_clip(client)
    health = client.get("/api/health").json()
    assert not health["capabilities"]["football_cv"]["available"]
    assert client.get("/api/cv/environment").json()["status"] == "not_configured"
    assert request(client, "/environment/check", status=503)["error"]["code"] == "cv_not_configured"
    assert client.get("/api/clips/" + clip["id"]).status_code == 200
    assert not list(client.app.state.catalog.settings.data_dir.rglob("*.pt"))


def test_geometry_known_projective_fit_and_reprojection():
    actual = [[1.2, 0.2, 40], [0.1, 0.9, 8], [0.001, 0.0002, 1]]
    source = [[0, 0], [200, 0], [200, 150], [0, 150], [80, 40], [25, 80]]
    target = [project(actual, *p) for p in source]
    fit, residual = homography(source, target)
    assert residual < 1e-8
    for point in source + [[97, 122]]:
        assert project(fit, *point) == pytest.approx(project(actual, *point), abs=1e-7)


@pytest.mark.parametrize("points", [[], [[1, 1]] * 4, [[i, i] for i in range(4)]])
def test_geometry_rejects_degenerate_correspondences(points):
    with pytest.raises(CatalogError):
        homography(points, points)


def test_calibration_preview_does_not_publish_and_stale_confirmation_fails(client, import_clip):
    clip = import_clip(client)
    body = cal_body(clip)
    prefix = f"/clips/{clip['id']}/calibrations"
    plan = request(client, prefix + "/preview", body)
    assert plan["reprojection_rms_pixels"] < 1e-7 and len(plan["grid"]) >= 20
    assert client.get("/api/cv" + prefix).json() == []
    saved = request(client, prefix, {**body, "preview_token": plan["preview_token"]}, 201)
    assert saved["revision"] == 1
    request(client, prefix, {**body, "preview_token": plan["preview_token"]}, 409)
    assert client.get("/api/clips/" + clip["id"]).json() == clip


@pytest.mark.parametrize(
    "change",
    [
        lambda b: b.update(source_sha256="0" * 64),
        lambda b: b.update(frame=99999),
        lambda b: b["points"][0].update(px=-1),
        lambda b: b["points"][0].update(fx=101),
        lambda b: b.update(points=[b["points"][0]] * 4),
    ],
)
def test_calibration_invalid_input_preserves_state(client, import_clip, change):
    clip = import_clip(client)
    body = cal_body(clip)
    change(body)
    result = client.post(f"/api/cv/clips/{clip['id']}/calibrations/preview", json=body)
    assert result.status_code in (409, 422), result.text
    assert client.get(f"/api/cv/clips/{clip['id']}/calibrations").json() == []


def test_preflight_missing_and_reviewed_context(client, import_clip):
    clip = import_clip(client)
    prefix = f"/clips/{clip['id']}/preflight"
    result = request(client, prefix, {"source_sha256": clip["source_sha256"]})
    assert result["status"] == "needs_input" and len(result["reasons"]) >= 4
    cal = save_cal(client, clip)
    body = {
        "source_sha256": clip["source_sha256"],
        "calibration_id": cal["id"],
        "snap_frame": 20,
        "context_reviewed": True,
        "offense_direction": "right",
        "offense_color": "light jerseys",
    }
    assert request(client, prefix, body)["status"] == "ready"
    for change in (
        {"snap_frame": 1},
        {"camera_cuts": [1.5]},
        {"context_start": 0.2},
        {"offense_direction": "unknown"},
    ):
        assert request(client, prefix, {**body, **change})["status"] == "needs_input"


def test_complete_analysis_preserves_raw_artifacts_and_source(client, import_clip, tmp_path):
    clip = import_clip(client)
    run = import_run(client, clip, tmp_path)
    assert "directory" not in run and run["manifest"]["origin"] == "synthetic_fixture"
    stored = CVStore(client.app.state.catalog)
    before = file_sha256(stored.artifact(run["id"], "positions.json"))
    projection = decision(client, run, value="CB")
    assert projection["raw_immutable"]
    assert projection["tracks"][0]["position"] == "LB"
    label = labels_at(projection["tracks"][0], projection["decisions"], 1)
    assert label["text"] == "CB" and label["reviewed"]
    assert file_sha256(stored.artifact(run["id"], "positions.json")) == before
    assert client.get("/api/clips/" + clip["id"]).json()["player_ids"] == []
    assert client.get(f"/api/cv/runs/{run['id']}/files/positions.json").status_code == 200


@pytest.mark.parametrize(
    "edit,code",
    [
        (lambda m, d: m.update(source_sha256="0" * 64), "analysis_source"),
        (lambda m, d: m["timeline"].update(pts_sha256="0" * 64), "analysis_transform"),
        (lambda m, d: m.update(transform_chain=[]), "analysis_transform"),
        (lambda m, d: d["positions.json"].update({"999": "QB"}), "analysis_positions"),
        (
            lambda m, d: d["tracks.json"]["tracks"][0]["frames"][0].update(box=[100, 20, 30, 40]),
            "analysis_tracks",
        ),
        (lambda m, d: d["detections.json"]["frames"].pop(), "analysis_frames"),
        (
            lambda m, d: d["field_coords.json"]["homographies"].__setitem__(0, None),
            "analysis_field",
        ),
        (lambda m, d: d["field_coords.json"]["tracks"][0].update(frames=[None]), "analysis_field"),
        (lambda m, d: d["snap.json"].update(frame=2), "analysis_snap"),
    ],
)
def test_invalid_analysis_fails_before_publication(client, import_clip, tmp_path, edit, code):
    clip = import_clip(client)
    archive = synthetic_bundle(client.app.state.catalog, clip, tmp_path, edit)
    result = client.post(
        f"/api/cv/clips/{clip['id']}/runs", files={"file": ("bundle.zip", archive.read_bytes())}
    )
    assert result.status_code in (409, 422), result.text
    assert result.json()["error"]["code"] == code
    assert not list(client.app.state.catalog.settings.path("analysis").iterdir())


def test_identity_intervals_revoke_and_stale_continuity_are_separate(client, import_clip, tmp_path):
    clip = import_clip(client)
    run = import_run(client, clip, tmp_path)
    player = client.post("/api/players", json={"name": "Receiver One"}).json()
    p = decision(client, run, "identity", player_id=player["id"], start_seconds=0.5, end_seconds=2)
    assert p["review_version"] == 1
    p = decision(client, {**run, "review_version": 1}, value="WR")
    roster = {player["id"]: player["name"]}
    assert (
        labels_at(p["tracks"][0], p["decisions"], 1, False, True, roster)["text"]
        == "WR · Receiver One"
    )
    assert (
        labels_at(p["tracks"][0], p["decisions"], 0.1, False, True, roster)["identity_id"] is None
    )
    p = decision(client, {**run, "review_version": 2}, "identity", player_id=None)
    assert labels_at(p["tracks"][0], p["decisions"], 1, False, True, roster)["identity_id"] is None
    p = decision(client, {**run, "review_version": 3}, "continuity", action="split", at_frame=30)
    assert {t["track_id"] for t in p["tracks"]} == {"1", "1@30", "2"}
    assert all(d["stale"] for d in p["decisions"] if d["kind"] != "continuity")
    assert client.get("/api/clips/" + clip["id"]).json()["player_ids"] == []


def test_cross_run_track_and_stale_review_rejected(client, import_clip, tmp_path):
    clip = import_clip(client)
    run = import_run(client, clip, tmp_path)
    decision(client, run, value="DL")
    body = {
        "expected_revision": 0,
        "start_seconds": 0,
        "end_seconds": 3,
        "value": "QB",
        "evidence": "manual",
    }
    request(client, f"/runs/{run['id']}/tracks/1/position", body, 409)
    request(client, f"/runs/{run['id']}/tracks/999/position", {**body, "expected_revision": 1}, 422)
    assert len(CVStore(client.app.state.catalog).decisions(run["id"])) == 1


def test_continuity_suppression_and_link_overlap_refused(client, import_clip, tmp_path):
    clip = import_clip(client)
    run = import_run(client, clip, tmp_path)
    request(
        client,
        f"/runs/{run['id']}/tracks/1/continuity",
        {"expected_revision": 0, "action": "link", "other_track_id": "2", "evidence": "reviewed"},
        422,
    )
    p = decision(client, run, "continuity", action="suppress")
    assert labels_at(p["tracks"][0], p["decisions"], 1, True) is None
    p = decision(client, {**run, "review_version": 1}, "continuity", action="restore")
    assert labels_at(p["tracks"][0], p["decisions"], 1, True)["text"] == "LB ?"


def test_transfer_is_a_reviewed_candidate_and_reject_preserves_target(client, import_clip):
    clip = import_clip(client)
    game = client.post("/api/games", json={"title": "Game", "opponent": "Opponent"}).json()
    clip = client.patch(
        "/api/clips/" + clip["id"], json={"version": 1, "game_id": game["id"]}
    ).json()
    cal = save_cal(client, clip)
    other = client.post(
        f"/api/study/clips/{clip['id']}/views",
        json={
            "version": clip["version"],
            "title": "Other view",
            "start_seconds": 0,
            "end_seconds": 3,
            "inherit_labels": True,
        },
    ).json()
    # Exact same frame is a deterministic transfer-geometry fixture, not auto-match evidence.
    body = {
        "source_calibration_id": cal["id"],
        "target_source_sha256": other["source_sha256"],
        "target_frame": 0,
        "target_expected_revision": 0,
        "pairs": [
            {
                "reference_x": p["px"],
                "reference_y": p["py"],
                "target_x": p["px"],
                "target_y": p["py"],
            }
            for p in cal_body(clip)["points"]
        ],
    }
    candidate = request(client, f"/clips/{other['id']}/calibration-transfer", body, 201)
    assert candidate["status"] == "needs_review"
    assert client.get(f"/api/cv/clips/{other['id']}/calibrations").json() == []
    request(
        client,
        f"/calibration-transfer/{candidate['id']}",
        {"preview_token": candidate["preview_token"], "decision": "rejected"},
    )
    assert client.get(f"/api/cv/clips/{other['id']}/calibrations").json() == []
    candidate = request(client, f"/clips/{other['id']}/calibration-transfer", body, 201)
    request(
        client,
        f"/calibration-transfer/{candidate['id']}",
        {"preview_token": candidate["preview_token"], "decision": "accepted"},
    )
    assert len(client.get(f"/api/cv/clips/{other['id']}/calibrations").json()) == 1


def wait(client, job):
    import time

    for _ in range(400):
        record = client.get("/api/work/" + job["id"]).json()
        if record["status"] not in ("queued", "running"):
            return record
        time.sleep(0.025)
    raise AssertionError("Task did not terminate")


def test_real_overlay_mp4_retains_audio_trim_and_provenance(client, import_clip, tmp_path):
    clip = import_clip(client)
    run = import_run(client, clip, tmp_path)
    decision(client, run, value="CB")
    job = request(
        client,
        f"/runs/{run['id']}/overlays",
        {"expected_review_revision": 1, "start_seconds": 0.4, "end_seconds": 2.4},
        202,
    )
    record = wait(client, job)
    assert record["status"] == "complete", record
    artifact = record["result"]
    assert artifact["manifest"]["rendered_frame_labels"] == 50
    assert artifact["manifest"]["source_sha256"] == clip["source_sha256"]
    video = tmp_path / "overlay.mp4"
    video.write_bytes(client.get(artifact["download_url"]).content)
    probe = json.loads(
        subprocess.run(
            ["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(video)],
            capture_output=True,
            check=True,
            timeout=10,
        ).stdout
    )
    assert float(probe["format"]["duration"]) == pytest.approx(2, abs=0.1)
    assert any(s["codec_type"] == "audio" for s in probe["streams"])
    assert file_sha256(client.app.state.catalog.source_path(clip["id"])) == clip["source_sha256"]
    decision(client, {**run, "review_version": 1}, value="LB")
    records = client.get("/api/artifacts").json()
    assert next(r for r in records if r["id"] == artifact["id"])["stale"]


def test_overlay_literal_text_never_becomes_ass_override():
    assert "{" not in safe_ass_text(r"{\pos(0,0)}\N<script>")
    assert "\\" not in safe_ass_text(r"{\pos(0,0)}\N<script>")


def dataset():
    return {
        "schema_version": 1,
        "profile": {"footage": "generated_fixture_only"},
        "plays": [
            {"play_id": "train", "source_sha256": "a" * 64, "split": "train", "labels": []},
            {
                "play_id": "test",
                "source_sha256": "b" * 64,
                "split": "test",
                "runtime_seconds": 2,
                "labels": [
                    {
                        "track_id": str(i),
                        "actual": a,
                        "predicted": p,
                        "reviewed_by": "fixture",
                        "evidence": "generator",
                    }
                    for i, (a, p) in enumerate([("LB", "LB"), ("WR", "CB"), ("QB", "unknown")])
                ],
            },
        ],
    }


def test_evaluation_counts_unknowns_per_class_and_never_claims_qualification():
    report = evaluate(dataset())
    assert report["accuracy"] == pytest.approx(1 / 3)
    assert report["unknown_count"] == 1 and report["per_class"]["LB"]["count"] == 1
    assert report["confusion_matrix"]["WR"]["CB"] == 1
    assert not report["profile_qualified"]
    assert report["mean_runtime_seconds"] == 2


def test_evaluation_disallows_same_source_split_leakage():
    data = dataset()
    data["plays"][1]["source_sha256"] = "a" * 64
    with pytest.raises(CatalogError, match="same source"):
        evaluate(data)


def test_subprocess_timeout_cancel_and_output_growth_are_bounded(tmp_path):
    for args, code in [
        ([sys.executable, "-c", "import time;time.sleep(5)"], "process_timeout"),
        ([sys.executable, "-c", 'print("X"*10000)'], "process_output_limit"),
    ]:
        with pytest.raises(CatalogError) as error:
            run_bounded(
                args, cwd=tmp_path, timeout=0.15 if code == "process_timeout" else 5, max_output=100
            )
        assert error.value.code == code
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(CatalogError) as error:
        run_bounded(
            [sys.executable, "-c", "import time;time.sleep(5)"], cwd=tmp_path, cancel=cancel
        )
    assert error.value.code == "job_cancelled"


def test_repeated_track_id_never_propagates_identity_and_revocation_updates_view(
    client, import_clip, tmp_path
):
    clip = import_clip(client)
    first = import_run(client, clip, tmp_path)
    second = import_run(client, clip, tmp_path)
    player = client.post(
        "/api/players", json={"name": "Example player", "jersey": "24", "position": "LB"}
    ).json()
    updated = decision(client, first, kind="identity", player_id=player["id"])
    other = client.get(f"/api/cv/runs/{second['id']}/projection").json()
    assert other["decisions"] == []
    route = f"/api/cv/players/{player['id']}/identities"
    found = client.get(route).json()
    assert len(found["items"]) == 1 and found["items"][0]["run_id"] == first["id"]
    assert not client.get("/api/clips", params={"player_id": player["id"]}).json()["items"]
    first["review_version"] = updated["review_version"]
    decision(client, first, kind="identity", player_id=None, start_seconds=1, end_seconds=2)
    found = client.get(route).json()["items"][0]["intervals"]
    assert [(i["start_seconds"], i["end_seconds"]) for i in found] == [(0, 1), (2, 3)]
    first["review_version"] += 1
    decision(client, first, kind="continuity", action="suppress")
    assert client.get(route).json()["items"] == []


def test_selected_portable_metadata_contains_only_selected_cv_decisions(
    client, import_clip, tmp_path
):
    from exais_football.portable import freeze_selection

    clip = import_clip(client)
    run = import_run(client, clip, tmp_path)
    decision(client, run, value="CB")
    frozen = freeze_selection(
        client.app.state.catalog, [{"clip_id": clip["id"], "version": clip["version"]}]
    )
    copied = frozen["clips"][0]["cv_runs"][0]
    assert copied["id"] == run["id"] and "directory" not in copied
    assert copied["decisions"][0]["payload"]["original_suggestion"] == "LB"
