from __future__ import annotations

import hashlib
import json

import pytest
from types import SimpleNamespace

from unity_extractor.object_models import ObjectRecord
from unity_extractor.resource_exporter import ResourceExporter


class FakeTextAssetReader:
    def __init__(self, payload, *, fail=False):
        self.payload = payload
        self.fail = fail

    def parse_as_object(self):
        if self.fail:
            raise ValueError("synthetic parse failure")
        return SimpleNamespace(m_Script=self.payload)


class FakeImage:
    def __init__(self, payload=b"fake-png", size=(16, 8), mode="RGBA"):
        self.payload = payload
        self.size = size
        self.mode = mode

    def save(self, path, format=None):
        assert format == "PNG"
        with open(path, "wb") as stream:
            stream.write(self.payload)


class FakeImageReader:
    def __init__(self, *, size=(16, 8), fail=False):
        self.fail = fail
        self.parsed = SimpleNamespace(
            image=FakeImage(size=size),
            m_Width=size[0],
            m_Height=size[1],
            m_TextureFormat=SimpleNamespace(name="RGBA32"),
        )

    def parse_as_object(self):
        if self.fail:
            raise ValueError("synthetic image decode failure")
        return self.parsed


def make_record(path_id: int, name: str | None = "chart", type_name="TextAsset") -> ObjectRecord:
    return ObjectRecord(
        resource_id=f"resource-{path_id:064d}"[-64:],
        source_id="game.apk!/assets/data.bundle",
        container_id="data.assets",
        path_id=path_id,
        type_name=type_name,
        name=name,
    )


def test_textasset_exports_original_bytes_and_manifest(tmp_path):
    payload = b"chart\xff\x00data"
    exporter = ResourceExporter(tmp_path)

    result = exporter.export_object(FakeTextAssetReader(payload), make_record(17))

    assert result is not None
    assert result["status"] == "exported"
    artifact = result["artifacts"][0]
    exported = tmp_path / artifact["path"]
    assert exported.read_bytes() == payload
    assert artifact["size_bytes"] == len(payload)
    assert len(artifact["sha256"]) == 64

    manifest_path = exporter.write_manifest()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["export_status"] == "completed"
    assert manifest["selected_object_count"] == 1
    assert manifest["exported_object_count"] == 1
    assert manifest["objects"][0]["resource_id"] == make_record(17).resource_id


def test_textasset_string_preserves_surrogateescaped_bytes(tmp_path):
    exporter = ResourceExporter(tmp_path)
    text = b"hello\xff".decode("utf-8", errors="surrogateescape")

    exporter.export_object(FakeTextAssetReader(text), make_record(18))

    artifact = exporter.records[0]["artifacts"][0]
    assert (tmp_path / artifact["path"]).read_bytes() == b"hello\xff"


def test_duplicate_names_and_untrusted_names_cannot_escape_output_dir(tmp_path):
    exporter = ResourceExporter(tmp_path)
    first = make_record(21, "../../same/name")
    second = make_record(22, "../../same/name")

    exporter.export_object(FakeTextAssetReader(b"one"), first)
    exporter.export_object(FakeTextAssetReader(b"two"), second)

    paths = [item["artifacts"][0]["path"] for item in exporter.records]
    assert len(set(paths)) == 2
    assert all(path.startswith("text/") for path in paths)
    assert all(".." not in path.split("/") for path in paths)
    assert all((tmp_path / path).is_file() for path in paths)


def test_one_failed_textasset_does_not_prevent_later_exports(tmp_path):
    exporter = ResourceExporter(tmp_path)

    failed = exporter.export_object(FakeTextAssetReader(b"", fail=True), make_record(31))
    succeeded = exporter.export_object(FakeTextAssetReader(b"ok"), make_record(32))

    assert failed["status"] == "failed"
    assert failed["warnings"]
    assert succeeded["status"] == "exported"
    assert exporter.summary()["failed_object_count"] == 1
    assert exporter.summary()["exported_object_count"] == 1


def test_texture2d_exports_png_and_records_dimensions_and_hash(tmp_path):
    exporter = ResourceExporter(tmp_path)
    record = make_record(51, "background", "Texture2D")

    result = exporter.export_object(FakeImageReader(), record)

    assert result["status"] == "exported"
    artifact = result["artifacts"][0]
    assert artifact["path"].startswith("images/texture2d/")
    assert artifact["path"].endswith(".png")
    output = tmp_path / artifact["path"]
    assert output.read_bytes() == b"fake-png"
    assert artifact["sha256"] == hashlib.sha256(b"fake-png").hexdigest()
    assert result["metadata"] == {
        "image_width": 16,
        "image_height": 8,
        "image_mode": "RGBA",
        "output_format": "PNG",
        "source_texture_format": "RGBA32",
        "source_width": 16,
        "source_height": 8,
    }


def test_sprite_exports_to_separate_directory(tmp_path):
    exporter = ResourceExporter(tmp_path)
    record = make_record(52, "icon", "Sprite")

    result = exporter.export_object(FakeImageReader(), record)

    assert result["status"] == "exported"
    assert result["artifacts"][0]["path"].startswith("images/sprite/")


def test_invalid_or_oversized_image_is_recorded_as_failed(tmp_path):
    exporter = ResourceExporter(tmp_path)
    invalid = exporter.export_object(
        FakeImageReader(size=(0, 8)), make_record(53, "empty", "Texture2D")
    )
    oversized = exporter.export_object(
        FakeImageReader(size=(100_001, 1000)), make_record(54, "huge", "Sprite")
    )

    assert invalid["status"] == "failed"
    assert "invalid dimensions" in invalid["warnings"][0]
    assert oversized["status"] == "failed"
    assert "safety limit" in oversized["warnings"][0]


def test_image_decode_failure_does_not_prevent_later_exports(tmp_path):
    exporter = ResourceExporter(tmp_path)
    failed = exporter.export_object(
        FakeImageReader(fail=True), make_record(55, "bad", "Texture2D")
    )
    succeeded = exporter.export_object(
        FakeImageReader(), make_record(56, "good", "Sprite")
    )

    assert failed["status"] == "failed"
    assert succeeded["status"] == "exported"


def test_unsupported_types_are_not_added_to_export_manifest(tmp_path):
    exporter = ResourceExporter(tmp_path)
    record = make_record(40)
    record.type_name = "Mesh"

    assert exporter.export_object(object(), record) is None
    assert exporter.records == []



def test_android_fmod_placeholder_allows_unitypy_image_export_import(monkeypatch):
    import sys
    import unity_extractor.resource_exporter as resource_exporter

    monkeypatch.setattr(resource_exporter.platform, "system", lambda: "Android")
    monkeypatch.delitem(sys.modules, "fmod_toolkit", raising=False)

    resource_exporter._prepare_unitypy_export_import()

    fmod_toolkit = sys.modules["fmod_toolkit"]
    with pytest.raises(RuntimeError, match="unavailable on Android"):
        fmod_toolkit.raw_to_wav(b"audio", "clip", 2, 44100)
