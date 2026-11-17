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


class FakeAudioClipReader:
    def __init__(self, samples, *, fail=False):
        self.fail = fail
        self.parsed = SimpleNamespace(
            samples=samples,
            m_Channels=2,
            m_Frequency=44100,
            m_Length=1.25,
            m_BitsPerSample=16,
            m_CompressionFormat=SimpleNamespace(name="Vorbis"),
            m_LoadType=SimpleNamespace(name="DecompressOnLoad"),
        )

    def parse_as_object(self):
        if self.fail:
            raise ValueError("synthetic audio decode failure")
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
    assert manifest["supported_types"] == ["TextAsset", "Texture2D", "Sprite", "AudioClip"]
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



def test_audioclip_exports_single_sample_and_metadata(tmp_path):
    exporter = ResourceExporter(tmp_path)
    payload = b"OggS-fake-audio"
    record = make_record(61, "bgm", "AudioClip")

    result = exporter.export_object(
        FakeAudioClipReader({"bgm.ogg": payload}), record
    )

    assert result["status"] == "exported"
    artifact = result["artifacts"][0]
    assert artifact["path"].startswith("audio/")
    assert artifact["path"].endswith(".ogg")
    assert artifact["sample_index"] == 0
    assert artifact["sample_name"] == "bgm.ogg"
    assert artifact["size_bytes"] == len(payload)
    assert artifact["sha256"] == hashlib.sha256(payload).hexdigest()
    assert (tmp_path / artifact["path"]).read_bytes() == payload
    assert result["metadata"] == {
        "sample_count": 1,
        "exported_sample_count": 1,
        "channels": 2,
        "sample_rate": 44100,
        "duration_seconds": 1.25,
        "bits_per_sample": 16,
        "source_compression_format": "Vorbis",
        "source_load_type": "DecompressOnLoad",
    }


def test_audioclip_multiple_samples_stay_linked_to_one_source_object(tmp_path):
    exporter = ResourceExporter(tmp_path)
    samples = {"song.wav": b"RIFF-one", "song-1.wav": b"RIFF-two"}
    record = make_record(62, "song", "AudioClip")

    result = exporter.export_object(FakeAudioClipReader(samples), record)

    assert result["status"] == "exported"
    assert len(result["artifacts"]) == 2
    paths = [artifact["path"] for artifact in result["artifacts"]]
    assert len(set(paths)) == 2
    assert [artifact["sample_index"] for artifact in result["artifacts"]] == [0, 1]
    assert all(path.startswith("audio/") and path.endswith(".wav") for path in paths)
    assert [ (tmp_path / path).read_bytes() for path in paths ] == [b"RIFF-one", b"RIFF-two"]
    assert result["metadata"]["sample_count"] == 2
    assert result["metadata"]["exported_sample_count"] == 2
    assert exporter.records[0]["resource_id"] == record.resource_id


def test_audioclip_sample_names_cannot_escape_output_dir(tmp_path):
    exporter = ResourceExporter(tmp_path)
    samples = {"../../evil.wav": b"one", "..\\..\\evil.ogg": b"two"}
    record = make_record(63, "../clip", "AudioClip")

    result = exporter.export_object(FakeAudioClipReader(samples), record)

    assert result["status"] == "exported"
    paths = [artifact["path"] for artifact in result["artifacts"]]
    assert all(path.startswith("audio/") for path in paths)
    assert all(".." not in path.split("/") for path in paths)
    assert len(set(paths)) == 2
    assert all((tmp_path / path).is_file() for path in paths)


def test_audioclip_colliding_sample_names_get_unique_paths(tmp_path):
    exporter = ResourceExporter(tmp_path)
    samples = {"same.wav": b"one", "same.WAV": b"two"}
    record = make_record(64, "clip", "AudioClip")

    result = exporter.export_object(FakeAudioClipReader(samples), record)

    paths = [artifact["path"] for artifact in result["artifacts"]]
    assert result["status"] == "exported"
    assert len(paths) == 2
    assert len(set(paths)) == 2


def test_partial_audioclip_failure_keeps_exported_samples_visible(tmp_path):
    exporter = ResourceExporter(tmp_path)
    samples = {"good.wav": b"good", "bad.wav": object(), "empty.wav": b""}
    record = make_record(65, "mixed", "AudioClip")

    result = exporter.export_object(FakeAudioClipReader(samples), record)

    assert result["status"] == "failed"
    assert len(result["artifacts"]) == 1
    assert len(result["warnings"]) == 2
    assert (tmp_path / result["artifacts"][0]["path"]).read_bytes() == b"good"


def test_audioclip_without_samples_is_recorded_as_failed(tmp_path):
    exporter = ResourceExporter(tmp_path)
    record = make_record(66, "silent", "AudioClip")

    result = exporter.export_object(FakeAudioClipReader({}), record)

    assert result["status"] == "failed"
    assert "did not expose any decodable samples" in result["warnings"][0]


def test_audioclip_unknown_sample_format_is_preserved_as_raw_bin(tmp_path):
    exporter = ResourceExporter(tmp_path)
    record = make_record(69, "voice", "AudioClip")

    result = exporter.export_object(
        FakeAudioClipReader({"voice.audioclip": b"raw-bytes"}), record
    )

    artifact = result["artifacts"][0]
    assert result["status"] == "exported"
    assert artifact["path"].endswith(".bin")
    assert artifact["format"] == "BIN"
    assert (tmp_path / artifact["path"]).read_bytes() == b"raw-bytes"


def test_audioclip_decode_failure_does_not_prevent_later_exports(tmp_path):
    exporter = ResourceExporter(tmp_path)
    failed = exporter.export_object(
        FakeAudioClipReader({}, fail=True), make_record(67, "bad", "AudioClip")
    )
    succeeded = exporter.export_object(
        FakeAudioClipReader({"ok.wav": b"ok"}), make_record(68, "good", "AudioClip")
    )

    assert failed["status"] == "failed"
    assert succeeded["status"] == "exported"


def test_android_fmod_placeholder_allows_unitypy_image_export_import(monkeypatch):
    import sys
    import unity_extractor.resource_exporter as resource_exporter

    monkeypatch.setattr(resource_exporter.platform, "system", lambda: "Android")
    previous = sys.modules.pop("fmod_toolkit", None)
    try:
        resource_exporter._prepare_unitypy_export_import()

        fmod_toolkit = sys.modules["fmod_toolkit"]
        with pytest.raises(RuntimeError, match="unavailable on Android"):
            fmod_toolkit.raw_to_wav(b"audio", "clip", 2, 44100)
    finally:
        sys.modules.pop("fmod_toolkit", None)
        if previous is not None:
            sys.modules["fmod_toolkit"] = previous
