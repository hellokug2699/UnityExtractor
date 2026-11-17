from __future__ import annotations

import base64
import io
import json
import struct
import zipfile
from pathlib import Path
from types import SimpleNamespace

from unity_extractor.enumerator import UnityObjectEnumerator


class FakeObject:
    def __init__(self, path_id: int, name: str, *, fail_name: bool = False):
        self.path_id = path_id
        self.type = SimpleNamespace(name="TextAsset")
        self.class_id = 49
        self.type_id = 0
        self.byte_size = 128
        self.assets_file = SimpleNamespace(
            name="sharedassets0.assets",
            parent=SimpleNamespace(name="main.bundle"),
            header=SimpleNamespace(version=22),
            unity_version="2022.3.62f2",
        )
        self._name = name
        self._fail_name = fail_name

    def peek_name(self):
        if self._fail_name:
            raise ValueError("synthetic name decode failure")
        return self._name

    @property
    def container(self):
        return f"assets/{self._name}.txt"


class FakeBackend:
    name = "FakeUnityPy"
    version = "test"

    def __init__(self, objects):
        self.objects = objects
        self.loaded_paths = []

    def load(self, source: Path):
        self.loaded_paths.append(source)
        return SimpleNamespace(objects=self.objects)


def test_enumerates_objects_and_duplicate_names_keep_distinct_ids(tmp_path):
    source = tmp_path / "fixture.bundle"
    source.write_bytes(b"not parsed by fake backend")
    backend = FakeBackend([FakeObject(101, "duplicate"), FakeObject(202, "duplicate")])

    report = UnityObjectEnumerator(backend=backend).enumerate(source)

    assert report.scan_status == "completed"
    assert len(report.objects) == 2
    assert report.objects[0].name == report.objects[1].name == "duplicate"
    assert report.objects[0].path_id != report.objects[1].path_id
    assert report.objects[0].resource_id != report.objects[1].resource_id
    assert report.objects[0].type_name == "TextAsset"
    assert report.objects[0].container_path == "assets/duplicate.txt"
    assert report.objects[0].metadata["unity_version"] == "2022.3.62f2"


def test_name_decode_failure_does_not_stop_other_objects(tmp_path):
    source = tmp_path / "fixture.assets"
    source.write_bytes(b"fixture")
    backend = FakeBackend([
        FakeObject(1, "broken", fail_name=True),
        FakeObject(2, "healthy"),
    ])

    report = UnityObjectEnumerator(backend=backend).enumerate(source)

    assert len(report.objects) == 2
    assert report.objects[0].name is None
    assert report.objects[0].warnings
    assert report.objects[1].name == "healthy"
    assert report.scan_status == "completed"
    assert report.warnings


def test_apk_member_is_staged_safely_and_source_path_is_preserved(tmp_path):
    apk = tmp_path / "game.apk"
    with zipfile.ZipFile(apk, "w") as archive:
        archive.writestr("assets/ab/level.bin", b"UnityFS" + b"\0" * 64)
        archive.writestr("assets/not-a-bundle.dat", b"plain data")

    backend = FakeBackend([FakeObject(77, "level")])
    report = UnityObjectEnumerator(backend=backend).enumerate(apk)

    assert report.inspected_containers == 1
    assert len(report.objects) == 1
    assert report.objects[0].source_id.endswith("!/assets/ab/level.bin")
    assert len(backend.loaded_paths) == 1
    assert not backend.loaded_paths[0].exists()


def test_object_limit_is_enforced(tmp_path):
    source = tmp_path / "fixture.bundle"
    source.write_bytes(b"fixture")
    backend = FakeBackend([FakeObject(1, "one"), FakeObject(2, "two")])

    report = UnityObjectEnumerator(backend=backend, max_objects=1).enumerate(source)

    assert len(report.objects) == 1
    assert any("Object limit reached" in warning for warning in report.warnings)


def test_resource_identity_ignores_random_staging_parent_name(tmp_path):
    source = tmp_path / "fixture.bundle"
    source.write_bytes(b"fixture")

    first = FakeObject(123, "stable")
    first.assets_file.parent.name = "unityextractor-random-a.bundle"
    second = FakeObject(123, "stable")
    second.assets_file.parent.name = "unityextractor-random-b.bundle"

    first_report = UnityObjectEnumerator(backend=FakeBackend([first])).enumerate(source)
    second_report = UnityObjectEnumerator(backend=FakeBackend([second])).enumerate(source)

    assert first_report.objects[0].container_id == "sharedassets0.assets"
    assert second_report.objects[0].container_id == "sharedassets0.assets"
    assert first_report.objects[0].resource_id == second_report.objects[0].resource_id


def test_non_candidate_files_do_not_consume_container_budget(tmp_path):
    root = tmp_path / "game"
    root.mkdir()
    (root / "large-unrelated.bin").write_bytes(b"x" * 4096)
    (root / "real.bundle").write_bytes(b"UnityFS" + b"\\0" * 57)
    backend = FakeBackend([FakeObject(9, "candidate")])

    report = UnityObjectEnumerator(
        backend=backend,
        max_container_bytes=1024,
        max_total_bytes=128,
    ).enumerate(root)

    assert report.inspected_containers == 1
    assert len(report.objects) == 1
    assert not any("Total container byte limit reached" in warning for warning in report.warnings)



class ReferenceBackend(FakeBackend):
    def iter_references(self, obj):
        if obj.path_id == 1:
            yield 0, 2, "sharedassets0.assets", True
            yield 4, 99, None, False


def test_reference_analysis_records_resolved_and_unresolved_pointers(tmp_path):
    source = tmp_path / "fixture.assets"
    source.write_bytes(b"fixture")
    backend = ReferenceBackend([FakeObject(1, "source"), FakeObject(2, "target")])

    report = UnityObjectEnumerator(backend=backend, include_references=True).enumerate(source)

    assert len(report.objects) == 2
    assert len(report.references) == 2
    resolved, unresolved = report.references
    assert resolved.source_resource_id == report.objects[0].resource_id
    assert resolved.target_resource_id == report.objects[1].resource_id
    assert resolved.target_path_id == 2
    assert resolved.file_id == 0
    assert resolved.resolved is True
    assert unresolved.target_resource_id is None
    assert unresolved.target_path_id == 99
    assert unresolved.file_id == 4
    assert unresolved.resolved is False


def test_reference_analysis_without_backend_support_is_a_warning(tmp_path):
    source = tmp_path / "fixture.assets"
    source.write_bytes(b"fixture")
    report = UnityObjectEnumerator(
        backend=FakeBackend([FakeObject(1, "source")]),
        include_references=True,
    ).enumerate(source)

    assert len(report.objects) == 1
    assert report.references == []
    assert any("not supported by the selected parser backend" in item for item in report.warnings)



def make_catalog(internal_ids: list[str], *, locator: str = "MainCatalog") -> bytes:
    keys = [f"key-{index}" for index in range(len(internal_ids))]
    key_data = struct.pack("<i", len(keys))
    key_offsets = []
    for key in keys:
        key_offsets.append(len(key_data))
        raw = key.encode("ascii")
        key_data += b"\x00" + struct.pack("<i", len(raw)) + raw

    bucket_data = struct.pack("<i", len(keys))
    for index, key_offset in enumerate(key_offsets):
        bucket_data += struct.pack("<iii", key_offset, 1, index)

    entry_data = struct.pack("<i", len(internal_ids))
    for index in range(len(internal_ids)):
        entry_data += struct.pack("<7i", index, 0, -1, 0, -1, index, 0)

    payload = {
        "m_LocatorId": locator,
        "m_InternalIds": internal_ids,
        "m_ProviderIds": ["AssetBundleProvider"],
        "m_KeyDataString": base64.b64encode(key_data).decode("ascii"),
        "m_BucketDataString": base64.b64encode(bucket_data).decode("ascii"),
        "m_EntryDataString": base64.b64encode(entry_data).decode("ascii"),
    }
    return json.dumps(payload).encode("utf-8")


def test_apk_catalog_is_inspected_alongside_unity_containers(tmp_path):
    apk = tmp_path / "game.apk"
    with zipfile.ZipFile(apk, "w") as archive:
        archive.writestr(
            "assets/aa/catalog.json",
            make_catalog([
                "{UnityEngine.AddressableAssets.Addressables.RuntimePath}/assets/ab/level.bin"
            ]),
        )
        archive.writestr("assets/ab/level.bin", b"UnityFS" + b"\0" * 64)

    report = UnityObjectEnumerator(
        backend=FakeBackend([FakeObject(77, "level")])
    ).enumerate(apk)

    assert report.inspected_containers == 1
    assert len(report.objects) == 1
    summary = report.addressables["summary"]
    assert summary["catalog_candidate_count"] == 1
    assert summary["validated_catalog_count"] == 1
    assert summary["internal_id_count"] == 1
    assert summary["matched_internal_id_count"] == 1
    assert summary["unresolved_internal_id_count"] == 0
    assert summary["key_mapping_status"] == "decoded"
    assert summary["key_entry_count"] == 1
    assert summary["matched_key_entry_count"] == 1
    assert report.to_dict()["addressables"] == report.addressables


def test_invalid_apk_catalog_does_not_abort_object_enumeration(tmp_path):
    apk = tmp_path / "game.apk"
    with zipfile.ZipFile(apk, "w") as archive:
        archive.writestr("assets/aa/catalog.json", b"{not json")
        archive.writestr("assets/ab/level.bin", b"UnityFS" + b"\0" * 64)

    report = UnityObjectEnumerator(
        backend=FakeBackend([FakeObject(78, "level")])
    ).enumerate(apk)

    assert report.scan_status == "completed"
    assert len(report.objects) == 1
    catalog = report.addressables["catalogs"][0]
    assert catalog["status"] == "failed"
    assert report.addressables["summary"]["validated_catalog_count"] == 0
    assert any("Catalog JSON is invalid" in warning for warning in report.warnings)


def test_unrelated_json_is_not_reported_as_addressables(tmp_path):
    apk = tmp_path / "game.apk"
    with zipfile.ZipFile(apk, "w") as archive:
        archive.writestr("assets/aa/settings.json", b'{"hello": 1}')
        archive.writestr("assets/ab/level.bin", b"UnityFS" + b"\0" * 64)

    report = UnityObjectEnumerator(
        backend=FakeBackend([FakeObject(79, "level")])
    ).enumerate(apk)

    assert report.addressables == {}
    assert len(report.objects) == 1


def test_catalog_json_without_markers_is_reported_but_not_validated(tmp_path):
    apk = tmp_path / "game.apk"
    with zipfile.ZipFile(apk, "w") as archive:
        archive.writestr("assets/aa/catalog.json", b'{"hello": 1}')

    report = UnityObjectEnumerator(backend=FakeBackend([])).enumerate(apk)

    summary = report.addressables["summary"]
    assert summary["catalog_candidate_count"] == 1
    assert summary["validated_catalog_count"] == 0
    assert report.addressables["catalogs"][0]["status"] == "candidate_unvalidated"


def test_directory_catalog_is_inspected_without_consuming_container_limits(tmp_path):
    root = tmp_path / "game"
    (root / "assets" / "aa").mkdir(parents=True)
    (root / "assets" / "aa" / "catalog.json").write_bytes(
        make_catalog(["assets/real.bundle"])
    )
    (root / "assets" / "real.bundle").write_bytes(b"UnityFS" + b"\0" * 64)

    report = UnityObjectEnumerator(
        backend=FakeBackend([FakeObject(80, "real")])
    ).enumerate(root)

    assert report.inspected_containers == 1
    assert len(report.objects) == 1
    summary = report.addressables["summary"]
    assert summary["validated_catalog_count"] == 1
    assert summary["matched_internal_id_count"] == 1


def test_object_callback_runs_before_staged_apk_member_is_removed(tmp_path):
    apk = tmp_path / "game.apk"
    with zipfile.ZipFile(apk, "w") as archive:
        archive.writestr("assets/data.bundle", b"UnityFS" + b"\\0" * 64)

    backend = FakeBackend([FakeObject(88, "chart")])
    callback_observations = []

    def callback(obj, record):
        callback_observations.append((obj.path_id, record.resource_id, backend.loaded_paths[-1].exists()))

    report = UnityObjectEnumerator(
        backend=backend,
        object_callback=callback,
    ).enumerate(apk)

    assert report.scan_status == "completed"
    assert callback_observations == [(88, report.objects[0].resource_id, True)]
    assert not backend.loaded_paths[0].exists()


def zip_bytes(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, payload in entries.items():
            archive.writestr(name, payload)
    return buffer.getvalue()


def test_nested_archive_members_and_catalogs_are_enumerated(tmp_path):
    inner = zip_bytes({
        "catalog.json": make_catalog([
            "{UnityEngine.AddressableAssets.Addressables.RuntimePath}/level.bundle"
        ]),
        "level.bundle": b"UnityFS" + b"\0" * 64,
    })
    apk = tmp_path / "game.apk"
    with zipfile.ZipFile(apk, "w") as archive:
        archive.writestr("assets/StreamingAssets/aa.zip", inner)

    backend = FakeBackend([FakeObject(90, "nested")])
    report = UnityObjectEnumerator(backend=backend).enumerate(apk)

    assert report.scan_status == "completed"
    assert report.inspected_containers == 1
    assert len(report.objects) == 1
    assert report.objects[0].source_id.endswith(
        "!/assets/StreamingAssets/aa.zip!/level.bundle"
    )
    summary = report.addressables["summary"]
    assert summary["validated_catalog_count"] == 1
    assert summary["matched_internal_id_count"] == 1
    assert summary["key_mapping_status"] == "decoded"
    assert not backend.loaded_paths[0].exists()


def test_directory_nested_archive_is_enumerated(tmp_path):
    root = tmp_path / "game"
    (root / "assets").mkdir(parents=True)
    (root / "assets" / "aa.zip").write_bytes(
        zip_bytes({"level.bundle": b"UnityFS" + b"\0" * 64})
    )
    backend = FakeBackend([FakeObject(91, "nested")])

    report = UnityObjectEnumerator(backend=backend).enumerate(root)

    assert report.inspected_containers == 1
    assert report.objects[0].source_id.endswith("!/assets/aa.zip!/level.bundle")


def test_nested_archive_depth_limit_is_reported(tmp_path):
    level_three = zip_bytes({"level.bundle": b"UnityFS" + b"\0" * 64})
    level_two = zip_bytes({"level3.zip": level_three})
    level_one = zip_bytes({"level2.zip": level_two})
    apk = tmp_path / "game.apk"
    with zipfile.ZipFile(apk, "w") as archive:
        archive.writestr("level1.zip", level_one)

    report = UnityObjectEnumerator(backend=FakeBackend([])).enumerate(apk)

    assert report.objects == []
    assert any(
        "Nested archive depth limit reached" in warning for warning in report.warnings
    )


def test_malformed_nested_archive_does_not_stop_outer_enumeration(tmp_path):
    apk = tmp_path / "game.apk"
    with zipfile.ZipFile(apk, "w") as archive:
        archive.writestr("assets/broken.zip", b"PK\x03\x04not-a-zip")
        archive.writestr("assets/level.bundle", b"UnityFS" + b"\0" * 64)

    report = UnityObjectEnumerator(
        backend=FakeBackend([FakeObject(92, "level")])
    ).enumerate(apk)

    assert len(report.objects) == 1
    assert any(
        "nested archive could not be inspected" in warning
        for warning in report.warnings
    )


def test_nested_archives_share_the_total_byte_budget(tmp_path):
    inner = zip_bytes({"level.bundle": b"UnityFS" + b"\0" * 64})
    apk = tmp_path / "game.apk"
    with zipfile.ZipFile(apk, "w") as archive:
        archive.writestr("assets/aa.zip", inner)

    report = UnityObjectEnumerator(
        backend=FakeBackend([FakeObject(93, "level")]),
        max_total_bytes=8,
    ).enumerate(apk)

    assert report.objects == []
    assert any(
        "Total container byte limit reached" in warning
        for warning in report.warnings
    )

