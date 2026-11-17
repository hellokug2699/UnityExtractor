from __future__ import annotations

import hashlib
import json
import zipfile

from unity_extractor.archive_validator import verify_export_archive


def _make_fixture(tmp_path, *, payload=b"asset-data", archive_payload=None, extra=False):
    export = tmp_path / "export"
    export.mkdir()
    (export / "text").mkdir()
    (export / "text" / "asset.txt").write_bytes(payload)
    manifest = {
        "schema_version": "1.0", "export_status": "completed",
        "selected_object_count": 1, "exported_object_count": 1, "failed_object_count": 0,
        "objects": [{"status": "exported", "artifacts": [{
            "path": "text/asset.txt", "size_bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        }]}],
    }
    manifest_path = export / "export-manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    archive_path = tmp_path / "export.zip"
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("text/asset.txt", payload if archive_payload is None else archive_payload)
        archive.write(manifest_path, "export-manifest.json")
        if extra:
            archive.writestr("unexpected.bin", b"extra")
    return archive_path, manifest_path


def test_archive_verification_passes_and_streams_manifest_files(tmp_path):
    archive, manifest = _make_fixture(tmp_path)
    result = verify_export_archive(archive, manifest)
    assert result["status"] == "passed"
    assert result["checked_file_count"] == 1
    assert result["checked_bytes"] == len(b"asset-data")


def test_archive_verification_detects_corrupted_payload(tmp_path):
    archive, manifest = _make_fixture(tmp_path, payload=b"expected", archive_payload=b"corrupt!")
    result = verify_export_archive(archive, manifest)
    assert result["status"] == "failed"
    assert any("SHA-256" in error for error in result["errors"])


def test_archive_verification_detects_unexpected_files(tmp_path):
    archive, manifest = _make_fixture(tmp_path, extra=True)
    result = verify_export_archive(archive, manifest)
    assert result["status"] == "failed"
    assert result["extra_files"] == ["unexpected.bin"]


def test_archive_verification_rejects_unsafe_manifest_paths(tmp_path):
    archive, manifest_path = _make_fixture(tmp_path)
    manifest = json.loads(manifest_path.read_text())
    manifest["objects"][0]["artifacts"][0]["path"] = "../outside"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    result = verify_export_archive(archive, manifest_path)
    assert result["status"] == "failed"
    assert any("Unsafe" in error for error in result["errors"])


def test_archive_verification_rejects_failed_export_records(tmp_path):
    archive, manifest_path = _make_fixture(tmp_path)
    manifest = json.loads(manifest_path.read_text())
    manifest["objects"][0]["status"] = "failed"
    manifest["objects"][0]["artifacts"] = []
    manifest["export_status"] = "partial"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    result = verify_export_archive(archive, manifest_path)
    assert result["status"] == "failed"
    assert result["failed_object_count"] == 1
