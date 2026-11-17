from __future__ import annotations

import json
import zipfile

from unity_extractor.scanner import UnityScanner, _looks_like_serialized_header


def test_non_unity_zip_is_unknown(tmp_path):
    apk = tmp_path / "plain.apk"
    with zipfile.ZipFile(apk, "w") as archive:
        archive.writestr("AndroidManifest.xml", b"not unity")
        archive.writestr("classes.dex", b"dex\n035\0")
    report = UnityScanner().scan(apk)
    assert report.engine_name == "unknown"
    assert report.engine_confidence == "unknown"
    assert report.scan_status == "completed"


def test_detects_unity_apk_and_il2cpp(tmp_path):
    apk = tmp_path / "game.apk"
    with zipfile.ZipFile(apk, "w") as archive:
        archive.writestr("lib/arm64-v8a/libunity.so", b"ELF")
        archive.writestr("lib/arm64-v8a/libil2cpp.so", b"ELF")
        archive.writestr("assets/bin/Data/globalgamemanagers", b"Unity version 2022.3.18f1")
        archive.writestr("assets/bin/Data/Managed/Metadata/global-metadata.dat", b"metadata")
        archive.writestr("assets/aa/catalog.json", b'{"m_LocatorId":"main"}')
    report = UnityScanner().scan(apk)
    assert report.engine_name == "Unity"
    assert report.engine_confidence == "high"
    assert report.scripting_backend == "IL2CPP"
    assert report.unity_version == "2022.3.18f1"
    assert any(f.kind == "addressables" for f in report.findings)


def test_detects_unityfs_standalone(tmp_path):
    bundle = tmp_path / "sharedassets.bundle"
    bundle.write_bytes(b"UnityFS\0" + b"\0" * 128)
    report = UnityScanner().scan(bundle)
    assert report.engine_name == "Unity"
    assert any(f.kind == "asset_bundle" and f.status == "detected" for f in report.findings)


def test_serialized_header_heuristic():
    import struct
    header = struct.pack(">IIII", 100, 1000, 17, 128) + b"\0" * 16
    assert _looks_like_serialized_header(header)
    assert not _looks_like_serialized_header(b"not a serialized file")


def test_json_report_is_serializable(tmp_path):
    folder = tmp_path / "game"
    (folder / "Game_Data" / "Managed").mkdir(parents=True)
    (folder / "Game_Data" / "Managed" / "Assembly-CSharp.dll").write_bytes(b"assembly")
    report = UnityScanner().scan(folder)
    payload = json.dumps(report.to_dict())
    assert '"schema_version": "1.0"' in payload


def test_linker_xml_does_not_imply_mono(tmp_path):
    apk = tmp_path / "il2cpp.apk"
    with zipfile.ZipFile(apk, "w") as archive:
        archive.writestr("lib/arm64-v8a/libunity.so", b"ELF")
        archive.writestr("lib/arm64-v8a/libil2cpp.so", b"ELF")
        archive.writestr("assets/aa/AddressablesLink/link.xml", b'<linker><assembly fullname="Assembly-CSharp" /></linker>')
    report = UnityScanner().scan(apk)
    assert report.scripting_backend == "IL2CPP"
    assert not any(f.kind == "mono" for f in report.findings)



def test_detects_cocos2d_x_runtime_without_calling_it_unity(tmp_path):
    apk = tmp_path / "cocos.apk"
    with zipfile.ZipFile(apk, "w") as archive:
        archive.writestr("lib/arm64-v8a/libcocos2dcpp.so", b"ELF")
        archive.writestr("assets/game/data.bin", b"opaque game data")
    report = UnityScanner().scan(apk)
    assert report.engine_name == "Cocos2d-x"
    assert report.engine_confidence == "high"
    assert any(f.kind == "other_engine_runtime" for f in report.findings)
    assert not any(f.kind == "unity_runtime" for f in report.findings)


def test_conflicting_strong_engine_fingerprints_remain_unknown(tmp_path):
    apk = tmp_path / "mixed.apk"
    with zipfile.ZipFile(apk, "w") as archive:
        archive.writestr("lib/arm64-v8a/libunity.so", b"ELF")
        archive.writestr("lib/arm64-v8a/libcocos2dcpp.so", b"ELF")
    report = UnityScanner().scan(apk)
    assert report.engine_name == "unknown"
    assert report.engine_confidence == "unknown"
    assert any("both found" in warning for warning in report.warnings)


def test_missing_input_is_reported_as_failed(tmp_path):
    report = UnityScanner().scan(tmp_path / "does-not-exist.apk")
    assert report.scan_status == "failed"
    assert report.errors


def test_directory_reports_oversized_files(tmp_path):
    oversized = tmp_path / "large.bin"
    oversized.write_bytes(b"x" * (2 * 1024 * 1024 + 1))
    report = UnityScanner().scan(tmp_path)
    assert report.scan_status == "completed"
    assert any("Skipped oversized file" in warning for warning in report.warnings)



def test_malformed_zip_like_input_does_not_crash(tmp_path):
    malformed = tmp_path / "broken.apk"
    malformed.write_bytes(b"PK\\x03\\x04 this is not a valid archive")
    report = UnityScanner().scan(malformed)
    assert report.scan_status == "completed"
    assert report.engine_name == "unknown"
