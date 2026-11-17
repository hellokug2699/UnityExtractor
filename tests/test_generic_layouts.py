from __future__ import annotations

import base64
import io
import json
import re
import struct
import zipfile
from pathlib import Path
from types import SimpleNamespace

from unity_extractor.adapters.phigros import PhigrosAdapter
from unity_extractor.enumerator import UnityObjectEnumerator

MARKER_RE = re.compile(rb"OBJ:(\d+):([A-Za-z0-9_]+):([A-Za-z0-9_.-]+)")
CORE_MODULES = (
    "scanner.py",
    "enumerator.py",
    "resource_exporter.py",
    "addressables.py",
    "preset.py",
    "object_models.py",
    "dependency_graph.py",
    "resource_classifier.py",
    "archive_validator.py",
)

CHART_JSON = json.dumps({
    "formatVersion": 3,
    "offset": 0.0,
    "numOfNotes": 1,
    "judgeLineList": [{
        "bpm": 180,
        "notesAbove": [{"type": 1, "time": 1.0}],
        "notesBelow": [],
    }],
})


def bundle_bytes(path_id: int, type_name: str, name: str) -> bytes:
    return b"UnityFS" + b"\0" * 8 + f"OBJ:{path_id}:{type_name}:{name}".encode("ascii")


def serialized_bytes(path_id: int, type_name: str, name: str) -> bytes:
    header = struct.pack(">IIII", 64, 256, 22, 32)
    marker = f"OBJ:{path_id}:{type_name}:{name}".encode("ascii")
    return header + b"\0" * (64 - len(header)) + marker


def zip_bytes(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, payload in entries.items():
            archive.writestr(name, payload)
    return buffer.getvalue()


def catalog_bytes(pairs: list[tuple[str, str]]) -> bytes:
    key_data = struct.pack("<i", len(pairs))
    offsets = []
    for key, _internal_id in pairs:
        offsets.append(len(key_data))
        raw = key.encode("ascii")
        key_data += b"\x00" + struct.pack("<i", len(raw)) + raw
    bucket_data = struct.pack("<i", len(pairs))
    for index, offset in enumerate(offsets):
        bucket_data += struct.pack("<iii", offset, 1, index)
    entry_data = struct.pack("<i", len(pairs))
    for index in range(len(pairs)):
        entry_data += struct.pack("<7i", index, 0, -1, 0, -1, index, 0)
    payload = {
        "m_LocatorId": "GenericCatalog",
        "m_InternalIds": [internal_id for _key, internal_id in pairs],
        "m_ProviderIds": ["AssetBundleProvider"],
        "m_KeyDataString": base64.b64encode(key_data).decode("ascii"),
        "m_BucketDataString": base64.b64encode(bucket_data).decode("ascii"),
        "m_EntryDataString": base64.b64encode(entry_data).decode("ascii"),
    }
    return json.dumps(payload).encode("utf-8")


class GenericObject:
    def __init__(self, path_id: int, type_name: str, name: str):
        self.path_id = path_id
        self.type = SimpleNamespace(name=type_name)
        self.class_id = 1
        self.type_id = 0
        self.byte_size = 16
        self.assets_file = SimpleNamespace(
            name=f"CAB-{path_id}",
            parent=SimpleNamespace(name="parent.bundle"),
            header=SimpleNamespace(version=22),
            unity_version="2022.3.62f2",
        )
        self._name = name

    def peek_name(self):
        return self._name

    @property
    def container(self):
        return None

    def parse_as_object(self):
        if self.type.name == "TextAsset":
            payload = CHART_JSON if self._name.startswith("Chart_") else "generic text"
            return SimpleNamespace(m_Script=payload)
        raise ValueError("Payload parsing is not needed for this fixture.")


class MarkerBackend:
    name = "MarkerBackend"
    version = "test"

    def __init__(self):
        self.loaded_paths: list[Path] = []

    def load(self, source: Path):
        self.loaded_paths.append(source)
        data = Path(source).read_bytes()
        match = MARKER_RE.search(data)
        if not match:
            raise ValueError("fixture marker not found")
        return SimpleNamespace(objects=[GenericObject(
            int(match.group(1)),
            match.group(2).decode("ascii"),
            match.group(3).decode("ascii"),
        )])


def build_generic_layout(tmp_path: Path, *, with_catalog: bool) -> Path:
    root = tmp_path / "game"
    (root / "assets" / "bundles").mkdir(parents=True)
    (root / "assets" / "aa").mkdir(parents=True)
    (root / "assets" / "StreamingAssets").mkdir(parents=True)
    (root / "assets" / "bundles" / "legacy.bundle").write_bytes(
        bundle_bytes(101, "TextAsset", "notes")
    )
    (root / "assets" / "standalone.assets").write_bytes(
        serialized_bytes(102, "Mesh", "standalone")
    )
    (root / "assets" / "StreamingAssets" / "pack.zip").write_bytes(
        zip_bytes({"nested.bundle": bundle_bytes(103, "Mesh", "nested")})
    )
    if with_catalog:
        (root / "assets" / "aa" / "catalog.json").write_bytes(catalog_bytes([
            (
                "Assets/Tracks/1001/Chart_IN.json",
                "{RuntimePath}/assets/bundles/legacy.bundle",
            ),
            (
                "Assets/Tracks/1001/music.wav",
                "{RuntimePath}/assets/standalone.assets",
            ),
        ]))
    return root


def test_generic_layouts_enumerate_without_any_game_adapter(tmp_path):
    root = build_generic_layout(tmp_path, with_catalog=False)
    backend = MarkerBackend()

    report = UnityObjectEnumerator(backend=backend).enumerate(root)

    assert report.scan_status == "completed"
    assert report.inspected_containers == 3
    assert {(obj.type_name, obj.name) for obj in report.objects} == {
        ("TextAsset", "notes"),
        ("Mesh", "standalone"),
        ("Mesh", "nested"),
    }
    assert report.addressables == {}
    assert "adapter" not in report.to_dict()


def test_addressables_and_non_addressables_layouts_coexist(tmp_path):
    root = build_generic_layout(tmp_path, with_catalog=True)

    report = UnityObjectEnumerator(backend=MarkerBackend()).enumerate(root)

    assert report.inspected_containers == 3
    assert len(report.objects) == 3
    summary = report.addressables["summary"]
    assert summary["validated_catalog_count"] == 1
    assert summary["internal_id_count"] == 2
    assert summary["matched_internal_id_count"] == 2
    assert summary["key_mapping_status"] == "decoded"
    assert len(report.catalog_key_entries) == 2


def test_optional_adapter_does_not_change_generic_enumeration(tmp_path):
    root = build_generic_layout(tmp_path, with_catalog=False)
    (root / "assets" / "bundles" / "chart.bundle").write_bytes(
        bundle_bytes(104, "TextAsset", "Chart_IN")
    )

    generic = UnityObjectEnumerator(backend=MarkerBackend()).enumerate(root)
    adapted = UnityObjectEnumerator(
        backend=MarkerBackend(),
        object_callback=PhigrosAdapter(),
    ).enumerate(root)

    def identity(report):
        return [
            (obj.resource_id, obj.source_id, obj.container_id, obj.path_id,
             obj.type_name, obj.name)
            for obj in report.objects
        ]

    assert identity(generic) == identity(adapted)
    assert generic.addressables == adapted.addressables
    assert generic.warnings == adapted.warnings
    assert "adapter" not in adapted.to_dict()

    adapter = PhigrosAdapter()
    UnityObjectEnumerator(backend=MarkerBackend(), object_callback=adapter).enumerate(
        root
    )
    analysis = adapter.analyze(adapted)
    assert analysis["detection"]["status"] == "detected"
    assert analysis["summary"]["unassociated_chart_count"] == 1


def test_core_modules_contain_no_game_specific_branches():
    source_root = Path(__file__).resolve().parents[1] / "src" / "unity_extractor"
    for module in CORE_MODULES:
        text = (source_root / module).read_text(encoding="utf-8").lower()
        assert "phigros" not in text, f"{module} contains a Phigros-specific branch"
