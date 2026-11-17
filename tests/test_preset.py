from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from unity_extractor.object_models import ObjectRecord
from unity_extractor.preset import load_plan_destinations
from unity_extractor.resource_exporter import ResourceExporter


class FakeTextAsset:
    def __init__(self, payload: bytes):
        self.payload = payload

    def parse_as_object(self):
        return SimpleNamespace(m_Script=self.payload)


class FakeImage:
    size = (8, 8)
    mode = "RGBA"

    def save(self, path, format=None):
        assert format == "PNG"
        with open(path, "wb") as stream:
            stream.write(b"fake-png")


class FakeImageReader:
    def parse_as_object(self):
        return SimpleNamespace(
            image=FakeImage(),
            m_Width=8,
            m_Height=8,
            m_TextureFormat=SimpleNamespace(name="RGBA32"),
        )


class FakeAudio:
    def __init__(self, samples):
        self.samples = samples

    def parse_as_object(self):
        return SimpleNamespace(
            samples=self.samples,
            m_Channels=2,
            m_Frequency=44100,
            m_Length=1.0,
            m_BitsPerSample=16,
            m_CompressionFormat=SimpleNamespace(name="Vorbis"),
            m_LoadType=SimpleNamespace(name="DecompressOnLoad"),
        )


def make_record(path_id: int, type_name: str, name: str) -> ObjectRecord:
    return ObjectRecord(
        resource_id=f"resource-{path_id}",
        source_id="game.apk!/assets/aa/Android/data.bundle",
        container_id="data.assets",
        path_id=path_id,
        type_name=type_name,
        name=name,
    )


def write_plan(tmp_path, entries):
    path = tmp_path / "plan.json"
    path.write_text(json.dumps({
        "adapter": {
            "export_preset": {"entries": entries},
        },
    }), encoding="utf-8")
    return path


def test_plan_loader_accepts_adapter_report_shape(tmp_path):
    plan = write_plan(tmp_path, [
        {
            "resource_id": "r1",
            "destination": "phigros/songs/1/charts/in.json",
        }
    ])

    assert load_plan_destinations(plan) == {
        "r1": "phigros/songs/1/charts/in.json"
    }


@pytest.mark.parametrize("destination", [
    "../escape.json",
    "/absolute.json",
    "phigros/../../escape.json",
    "",
])
def test_plan_loader_rejects_unsafe_destinations(tmp_path, destination):
    plan = write_plan(tmp_path, [
        {"resource_id": "r1", "destination": destination}
    ])

    with pytest.raises(ValueError):
        load_plan_destinations(plan)


def test_plan_loader_rejects_duplicate_destinations(tmp_path):
    plan = write_plan(tmp_path, [
        {"resource_id": "r1", "destination": "same.json"},
        {"resource_id": "r2", "destination": "same.json"},
    ])

    with pytest.raises(ValueError, match="Duplicate"):
        load_plan_destinations(plan)


def test_textasset_uses_plan_destination(tmp_path):
    exporter = ResourceExporter(
        tmp_path,
        destination_map={"resource-1": "phigros/songs/1/charts/in.json"},
    )

    result = exporter.export_object(
        FakeTextAsset(b'{"formatVersion": 3}'),
        make_record(1, "TextAsset", "Chart_IN"),
    )

    assert result["status"] == "exported"
    assert result["artifacts"][0]["path"] == "phigros/songs/1/charts/in.json"
    assert (tmp_path / "phigros/songs/1/charts/in.json").read_bytes() == b'{"formatVersion": 3}'


def test_image_uses_plan_destination_and_requires_png(tmp_path):
    exporter = ResourceExporter(
        tmp_path,
        destination_map={
            "resource-2": "phigros/songs/1/illustrations/cover.png",
            "resource-3": "phigros/songs/1/illustrations/cover.jpg",
        },
    )

    valid = exporter.export_object(
        FakeImageReader(), make_record(2, "Texture2D", "Illustration")
    )
    invalid = exporter.export_object(
        FakeImageReader(), make_record(3, "Texture2D", "Illustration")
    )

    assert valid["status"] == "exported"
    assert (tmp_path / "phigros/songs/1/illustrations/cover.png").is_file()
    assert invalid["status"] == "failed"
    assert any("must end with .png" in item for item in invalid["warnings"])


def test_audio_uses_plan_destination_for_multiple_samples(tmp_path):
    exporter = ResourceExporter(
        tmp_path,
        destination_map={"resource-4": "phigros/songs/1/audio/music.wav"},
    )

    result = exporter.export_object(
        FakeAudio({"music.wav": b"one", "music-1.wav": b"two"}),
        make_record(4, "AudioClip", "music"),
    )

    paths = [artifact["path"] for artifact in result["artifacts"]]
    assert result["status"] == "exported"
    assert paths == [
        "phigros/songs/1/audio/music.wav",
        "phigros/songs/1/audio/music_1.wav",
    ]
    assert (tmp_path / paths[0]).read_bytes() == b"one"
    assert (tmp_path / paths[1]).read_bytes() == b"two"


def test_selected_resource_ids_limit_export_to_plan_entries(tmp_path):
    exporter = ResourceExporter(
        tmp_path,
        destination_map={"resource-6": "phigros/songs/1/charts/in.json"},
        selected_resource_ids={"resource-6"},
    )

    selected = exporter.export_object(
        FakeTextAsset(b"chart"), make_record(6, "TextAsset", "Chart_IN")
    )
    skipped = exporter.export_object(
        FakeTextAsset(b"other"), make_record(7, "TextAsset", "Other")
    )

    assert selected["status"] == "exported"
    assert skipped is None
    assert len(exporter.records) == 1


def test_unsafe_destination_map_is_recorded_as_failed(tmp_path):
    exporter = ResourceExporter(
        tmp_path,
        destination_map={"resource-5": "../escape.json"},
    )

    result = exporter.export_object(
        FakeTextAsset(b"x"), make_record(5, "TextAsset", "Chart_IN")
    )

    assert result["status"] == "failed"
    assert any("Unsafe preset destination" in item for item in result["warnings"])
