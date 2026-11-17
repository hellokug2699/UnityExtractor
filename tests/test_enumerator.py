from __future__ import annotations

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
