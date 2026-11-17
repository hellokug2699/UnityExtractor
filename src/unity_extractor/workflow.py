"""Shared scan workflow used by the CLI and desktop frontends."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from .object_models import EnumerationReport
from .resource_exporter import ResourceExporter
from .scanner import UnityScanner


@dataclass
class ScanRequest:
    input_path: str
    enumerate_objects: bool = False
    analyze_references: bool = False
    analyze_dependencies: bool = False
    dependency_root: str | None = None
    dependency_depth: int = 8
    max_container_mb: int = 512
    export_resources: bool = False
    export_dir: str | None = None
    apply_plan: str | None = None
    adapter: Any | None = None
    catalog_paths: list[str] = field(default_factory=list)
    selected_resource_ids: set[str] | None = None
    backend: Any | None = None
    cancel_event: Any | None = None
    progress_callback: Callable[[str, int, int | None], None] | None = None


def _make_callback(exporter: ResourceExporter | None, adapter: Any | None) -> Any | None:
    callbacks = [item for item in (exporter, adapter) if item is not None]
    if not callbacks:
        return None
    if len(callbacks) == 1:
        return callbacks[0]

    def callback(obj: Any, record: Any) -> None:
        for item in callbacks:
            item(obj, record)

    return callback


def run_scan(request: ScanRequest) -> dict[str, Any]:
    """Run a scan and return a JSON-safe report.

    This function owns no game-specific rules. Adapters are supplied by the
    frontend and only observe already-canonical records.
    """
    if request.export_resources and not request.export_dir:
        raise ValueError("--export-resources requires --export-dir.")
    if request.apply_plan and not request.export_dir:
        raise ValueError("--apply-plan requires --export-dir.")
    if request.selected_resource_ids is not None and not request.export_dir:
        raise ValueError("Selective export requires --export-dir.")
    if request.analyze_dependencies and not request.analyze_references:
        raise ValueError("--analyze-dependencies requires --analyze-references.")
    if request.dependency_root and not request.analyze_dependencies:
        raise ValueError("--dependency-root requires --analyze-dependencies.")
    if request.dependency_depth < 0:
        raise ValueError("--dependency-depth must be zero or greater.")
    if request.enumerate_objects is False and (
        request.export_resources
        or request.apply_plan
        or request.catalog_paths
        or request.adapter is not None
        or request.analyze_references
        or request.analyze_dependencies
    ):
        raise ValueError("These options require object enumeration.")

    if not request.enumerate_objects:
        return UnityScanner().scan(request.input_path).to_dict()

    from .dependency_graph import build_dependency_graph
    from .enumerator import UnityObjectEnumerator
    from .preset import load_plan_destinations

    destination_map: dict[str, str] | None = None
    if request.apply_plan:
        destination_map = load_plan_destinations(request.apply_plan)

    exporter: ResourceExporter | None = None
    selected = request.selected_resource_ids
    if request.apply_plan and not request.export_resources and selected is None:
        selected = set(destination_map or {})
    if request.export_resources or request.apply_plan or selected is not None:
        exporter = ResourceExporter(
            request.export_dir,
            destination_map=destination_map,
            selected_resource_ids=selected,
        )

    enumerator = UnityObjectEnumerator(
        backend=request.backend,
        max_container_bytes=max(1, request.max_container_mb) * 1024 * 1024,
        include_references=request.analyze_references,
        object_callback=_make_callback(exporter, request.adapter),
        catalog_paths=request.catalog_paths,
        cancel_event=request.cancel_event,
        progress_callback=request.progress_callback,
    )
    enumeration: EnumerationReport = enumerator.enumerate(request.input_path)
    report = enumeration.to_dict()
    if request.adapter is not None:
        report["adapter"] = request.adapter.analyze(enumeration)

    if exporter is not None:
        try:
            manifest_path = exporter.write_manifest()
            report["resource_export"] = exporter.summary() | {
                "manifest_path": str(manifest_path)
            }
            if destination_map:
                exported_ids = {item["resource_id"] for item in exporter.records}
                missing = sorted(set(destination_map) - exported_ids)
                report["resource_export"]["missing_plan_resource_count"] = len(missing)
                report["resource_export"]["missing_plan_resource_samples"] = missing[:20]
        except OSError as exc:
            report.setdefault("errors", []).append(
                f"Could not write export manifest: {exc}"
            )
            report["scan_status"] = "partial" if report.get("objects") else "failed"

    if request.analyze_dependencies:
        report["dependency_graph"] = build_dependency_graph(
            report["objects"],
            report["references"],
            root_resource_id=request.dependency_root,
            max_depth=request.dependency_depth,
        )
    return report
