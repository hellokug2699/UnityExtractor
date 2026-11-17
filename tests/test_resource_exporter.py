from __future__ import annotations

import json
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


def make_record(path_id: int, name: str | None = "chart") -> ObjectRecord:
    return ObjectRecord(
        resource_id=f"resource-{path_id:064d}"[-64:],
        source_id="game.apk!/assets/data.bundle",
        container_id="data.assets",
        path_id=path_id,
        type_name="TextAsset",
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


def test_unsupported_types_are_not_added_to_export_manifest(tmp_path):
    exporter = ResourceExporter(tmp_path)
    record = make_record(40)
    record.type_name = "Texture2D"

    assert exporter.export_object(object(), record) is None
    assert exporter.records == []
