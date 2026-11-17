from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

from unity_extractor.adapters.registry import available_adapters, create_adapter
from unity_extractor.platforms.desktop.controller import (
    DesktopController,
    DesktopOptions,
)
from unity_extractor.workflow import ScanRequest, run_scan


class DesktopObject:
    def __init__(self, path_id: int, name: str, payload: bytes):
        self.path_id = path_id
        self.type = SimpleNamespace(name="TextAsset")
        self.class_id = 49
        self.type_id = 0
        self.byte_size = len(payload)
        self.assets_file = SimpleNamespace(
            name=f"CAB-{path_id}",
            parent=SimpleNamespace(name="parent.bundle"),
            header=SimpleNamespace(version=22),
            unity_version="2022.3.62f2",
        )
        self._name = name
        self._payload = payload

    def peek_name(self):
        return self._name

    @property
    def container(self):
        return None

    def parse_as_object(self):
        return SimpleNamespace(m_Script=self._payload)


class DesktopBackend:
    name = "DesktopBackend"
    version = "test"

    def __init__(self, objects):
        self.objects = objects

    def load(self, source):
        return SimpleNamespace(objects=self.objects)


def make_input(tmp_path):
    root = tmp_path / "game"
    root.mkdir()
    (root / "data.bundle").write_bytes(b"UnityFS" + b"\0" * 64)
    return root


def test_controller_scans_and_exposes_objects(tmp_path):
    controller = DesktopController(
        backend=DesktopBackend([
            DesktopObject(1, "alpha", b"one"),
            DesktopObject(2, "beta", b"two"),
        ])
    )
    options = DesktopOptions(input_path=str(make_input(tmp_path)))

    report = controller.run(options)

    assert report["scan_status"] == "completed"
    assert len(report["objects"]) == 2
    assert controller.summarize(report)[1].startswith("Status:")


def test_controller_filters_objects_and_reports_truncation():
    objects = [
        {"resource_id": "1", "type_name": "TextAsset", "resource_category": "data",
         "name": "alpha", "source_id": "a.bundle", "container_id": "c", "path_id": 1},
        {"resource_id": "2", "type_name": "Texture2D", "resource_category": "image",
         "name": "beta", "source_id": "b.bundle", "container_id": "c", "path_id": 2},
    ]

    rows, truncated = DesktopController.filter_objects(objects, category="image")
    assert [row["resource_id"] for row in rows] == ["2"]
    assert truncated is False

    rows, _ = DesktopController.filter_objects(objects, text="ALPHA")
    assert [row["resource_id"] for row in rows] == ["1"]

    rows, truncated = DesktopController.filter_objects(objects, limit=1)
    assert len(rows) == 1
    assert truncated is True


def test_controller_filter_values_are_sorted_and_unique():
    objects = [
        {"resource_category": "image", "type_name": "Texture2D"},
        {"resource_category": "data", "type_name": "TextAsset"},
        {"resource_category": "image", "type_name": "Sprite"},
    ]

    values = DesktopController.filter_values(objects)

    assert values == {
        "categories": ["data", "image"],
        "types": ["Sprite", "TextAsset", "Texture2D"],
    }


def test_controller_exports_only_selected_resources(tmp_path):
    root = make_input(tmp_path)
    backend = DesktopBackend([
        DesktopObject(1, "alpha", b"one"),
        DesktopObject(2, "beta", b"two"),
    ])
    controller = DesktopController(backend=backend)
    options = DesktopOptions(input_path=str(root))

    initial = controller.run(options)
    selected = {initial["objects"][0]["resource_id"]}

    options.export_dir = str(tmp_path / "export")
    report = controller.run(options, selected_resource_ids=selected)

    assert report["resource_export"]["exported_object_count"] == 1
    assert report["resource_export"]["selected_object_count"] == 1
    assert len(list((tmp_path / "export").rglob("*.txt"))) == 1


def test_controller_cancellation_returns_cancelled_report(tmp_path):
    cancel_event = threading.Event()
    cancel_event.set()
    controller = DesktopController(
        backend=DesktopBackend([DesktopObject(1, "alpha", b"one")])
    )

    report = controller.run(
        DesktopOptions(input_path=str(make_input(tmp_path))),
        cancel_event=cancel_event,
    )

    assert report["scan_status"] == "cancelled"
    assert report["objects"] == []


def test_workflow_scanner_mode_does_not_require_enumeration(tmp_path):
    report = run_scan(ScanRequest(input_path=str(tmp_path)))

    assert report["scan_status"] in {"completed", "partial", "failed"}
    assert "objects" not in report


def test_workflow_rejects_enumeration_only_options():
    with pytest.raises(ValueError):
        run_scan(ScanRequest(input_path="input", export_resources=True))
    with pytest.raises(ValueError):
        run_scan(ScanRequest(
            input_path="input",
            enumerate_objects=True,
            analyze_dependencies=True,
        ))


def test_adapter_registry_is_lazy_and_rejects_unknown_names():
    assert "phigros" in available_adapters()
    adapter = create_adapter("phigros")
    assert adapter.name == "phigros"
    assert create_adapter(None) is None
    with pytest.raises(ValueError):
        create_adapter("unknown")


def test_desktop_entrypoint_reports_missing_display(monkeypatch, capsys):
    from unity_extractor.platforms.desktop import app

    def no_display():
        raise app.tk.TclError("no display")

    monkeypatch.setattr(app.tk, "Tk", no_display)

    assert app.main() == 2
    assert "requires a display" in capsys.readouterr().out
