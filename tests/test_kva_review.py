"""Native-format interoperability fixtures; real Windows round-trip is a separate gate."""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from exais_football.frames import frame_index


def kva(client, clip, title="New KVA title", comment="Correct the first step", wrong=None):
    index = frame_index(client.app.state.catalog, clip["id"], create=True)
    root = ET.Element("KinoveaVideoAnalysis")
    fields = {
        "FormatVersion": "2.1",
        "Producer": "Kinovea.2025.2.0",
        "OriginalFilename": clip["original_filename"].rsplit(".", 1)[0],
        "FullPath": "C:\\never-read\\external.mp4",
        "ByteLength": str(client.app.state.catalog.source_path(clip["id"]).stat().st_size),
        "GlobalTitle": title,
        "ImageSize": "320;180",
        "FirstTimeStamp": str(index["raw_timestamps"][0]),
        "SelectionStart": str(index["raw_timestamps"][10]),
        "SelectionEnd": str(index["raw_timestamps"][59]),
        "TimeOrigin": str(index["raw_timestamps"][20]),
        "VideoFramerate": "25",
        "AverageTimeStampsPerFrame": "512",
    }
    if wrong:
        fields.update(wrong)
    for key, value in fields.items():
        ET.SubElement(root, key).text = value
    key = ET.SubElement(ET.SubElement(root, "Keyframes"), "Keyframe", id="keyframe-one")
    ET.SubElement(key, "Timestamp").text = str(index["raw_timestamps"][25])
    ET.SubElement(key, "Name").text = "Release"
    ET.SubElement(key, "Comment").text = comment
    ET.SubElement(ET.SubElement(key, "Drawings"), "UnknownDrawing").text = "preserve this"
    return ET.tostring(root, encoding="utf-8")


def upload(client, clip, content):
    response = client.post(
        f"/api/clips/{clip['id']}/kinovea/annotations",
        files={"file": ("clip.kva", content, "application/xml")},
    )
    assert response.status_code == 201, response.text


def preview(client, clip, status=200):
    response = client.post(
        f"/api/study/clips/{clip['id']}/kva/preview", json={"source_confirmed": True}
    )
    assert response.status_code == status, response.text
    return response.json()


def apply(client, clip, plan, choices=None, status=200, decision="accepted"):
    response = client.post(
        f"/api/study/clips/{clip['id']}/kva/apply",
        json={
            "source_confirmed": True,
            "preview_token": plan["preview_token"],
            "decision": decision,
            "choices": choices or {f["key"]: f["recommended"] for f in plan["fields"]},
        },
    )
    assert response.status_code == status, response.text
    return response.json()


def test_kva_exact_pts_import_raw_preserved_and_idempotent(client, import_clip):
    clip = import_clip(client)
    raw = kva(client, clip)
    upload(client, clip, raw)
    plan = preview(client, clip)
    assert plan["incoming"]["range"] == {"start_seconds": 0.4, "end_seconds": 2.4}
    assert plan["incoming"]["notes"]["keyframe-one"]["at_seconds"] == 1
    assert "Keyframes/Drawings" in plan["incoming"]["unsupported_preserved"]
    result = apply(client, clip, plan)
    assert result["clip"]["title"] == "New KVA title"
    assert client.get(f"/api/clips/{clip['id']}/notes").json()[0]["at_seconds"] == 1
    plan = preview(client, clip)
    apply(client, clip, plan)
    assert len(client.get(f"/api/clips/{clip['id']}/notes").json()) == 1
    assert client.get(f"/api/clips/{clip['id']}/kinovea/annotations").content == raw
    assert len(client.get(f"/api/study/clips/{clip['id']}/kva/history").json()) == 1


def test_kva_three_way_local_change_kept_external_change_conflicts(client, import_clip):
    clip = import_clip(client)
    upload(client, clip, kva(client, clip))
    result = apply(client, clip, preview(client, clip))
    current = result["clip"]
    current = client.patch(
        "/api/clips/" + clip["id"], json={"version": current["version"], "title": "Local title"}
    ).json()
    plan = preview(client, clip)
    title = next(f for f in plan["fields"] if f["key"] == "title")
    assert title["recommended"] == "catalog" and title["local_changed"]
    apply(client, clip, plan)
    assert client.get("/api/clips/" + clip["id"]).json()["title"] == "Local title"
    upload(client, clip, kva(client, clip, title="Another KVA edit"))
    plan = preview(client, clip)
    assert next(f for f in plan["fields"] if f["key"] == "title")["conflict"]
    choices = {f["key"]: "catalog" for f in plan["fields"]}
    apply(client, clip, plan, choices)
    assert client.get("/api/clips/" + clip["id"]).json()["title"] == "Local title"


def test_kva_changed_sidecar_and_catalog_invalidate_preview(client, import_clip):
    clip = import_clip(client)
    upload(client, clip, kva(client, clip))
    plan = preview(client, clip)
    client.patch("/api/clips/" + clip["id"], json={"version": clip["version"], "favorite": True})
    apply(client, clip, plan, status=409)
    plan = preview(client, clip)
    upload(client, clip, kva(client, clip, comment="External changed"))
    apply(client, clip, plan, status=409)
    assert client.get(f"/api/clips/{clip['id']}/notes").json() == []


@pytest.mark.parametrize(
    "wrong",
    [
        {"OriginalFilename": "different-source"},
        {"ByteLength": "1"},
        {"FirstTimeStamp": "123"},
        {"SelectionStart": "999999"},
        {"FormatVersion": "2.0"},
        {"Producer": "OtherTool.1.0"},
    ],
)
def test_kva_wrong_or_unsupported_context_does_not_import(client, import_clip, wrong):
    clip = import_clip(client)
    raw = kva(client, clip, wrong=wrong)
    upload(client, clip, raw)
    response = client.post(
        f"/api/study/clips/{clip['id']}/kva/preview", json={"source_confirmed": True}
    )
    assert response.status_code in (409, 422), response.text
    assert client.get("/api/clips/" + clip["id"]).json() == clip
    assert client.get(f"/api/clips/{clip['id']}/kinovea/annotations").content == raw


def test_kva_reject_and_incomplete_choices_are_non_mutating(client, import_clip):
    clip = import_clip(client)
    upload(client, clip, kva(client, clip))
    plan = preview(client, clip)
    apply(client, clip, plan, {"title": "kva"}, status=422)
    apply(client, clip, plan, decision="rejected")
    assert client.get("/api/clips/" + clip["id"]).json() == clip
    assert client.get(f"/api/clips/{clip['id']}/notes").json() == []
