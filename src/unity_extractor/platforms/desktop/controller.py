"""Headless desktop controller. Tkinter is only imported by the view."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Iterable

from ...adapters.registry import create_adapter
from ...workflow import ScanRequest, run_scan

MAX_BROWSER_ROWS = 5000


@dataclass
class DesktopOptions:
    input_path: str
    enumerate_objects: bool = True
    analyze_references: bool = False
    analyze_dependencies: bool = False
    dependency_root: str | None = None
    dependency_depth: int = 8
    export_resources: bool = False
    export_dir: str | None = None
    adapter_name: str | None = None
    catalog_paths: list[str] = field(default_factory=list)
    max_container_mb: int = 512


class DesktopController:
    def __init__(self, backend: Any | None = None) -> None:
        self.backend = backend

    def run(
        self,
        options: DesktopOptions,
        *,
        cancel_event: Any | None = None,
        progress_callback: Callable[[str, int, int | None], None] | None = None,
        selected_resource_ids: set[str] | None = None,
    ) -> dict[str, Any]:
        request = ScanRequest(
            input_path=options.input_path,
            enumerate_objects=options.enumerate_objects,
            analyze_references=options.analyze_references,
            analyze_dependencies=options.analyze_dependencies,
            dependency_root=options.dependency_root,
            dependency_depth=options.dependency_depth,
            max_container_mb=options.max_container_mb,
            export_resources=options.export_resources or selected_resource_ids is not None,
            export_dir=options.export_dir,
            adapter=create_adapter(options.adapter_name),
            catalog_paths=list(options.catalog_paths),
            selected_resource_ids=selected_resource_ids,
            backend=self.backend,
            cancel_event=cancel_event,
            progress_callback=progress_callback,
        )
        return run_scan(request)

    @staticmethod
    def filter_objects(
        objects: Iterable[dict[str, Any]],
        *,
        category: str = "all",
        type_name: str = "all",
        text: str = "",
        limit: int | None = MAX_BROWSER_ROWS,
    ) -> tuple[list[dict[str, Any]], bool]:
        needle = text.strip().casefold()
        rows: list[dict[str, Any]] = []
        truncated = False
        for obj in objects:
            if category != "all" and obj.get("resource_category") != category:
                continue
            if type_name != "all" and obj.get("type_name") != type_name:
                continue
            if needle:
                haystack = " ".join(str(obj.get(key) or "") for key in (
                    "name",
                    "type_name",
                    "container_path",
                    "resource_id",
                    "source_id",
                    "container_id",
                )).casefold()
                if needle not in haystack:
                    continue
            if limit is not None and len(rows) >= limit:
                truncated = True
                break
            rows.append(obj)
        return rows, truncated

    @staticmethod
    def filter_values(objects: Iterable[dict[str, Any]]) -> dict[str, list[str]]:
        categories = sorted({
            str(obj.get("resource_category"))
            for obj in objects
            if obj.get("resource_category")
        })
        types = sorted({
            str(obj.get("type_name"))
            for obj in objects
            if obj.get("type_name")
        })
        return {"categories": categories, "types": types}

    @staticmethod
    def summarize(report: dict[str, Any]) -> list[str]:
        lines = [
            f"Input: {report.get('input_path', '')}",
            f"Status: {report.get('scan_status', 'unknown')}",
        ]
        if "engine_name" in report:
            lines.append(
                f"Engine: {report.get('engine_name')} "
                f"({report.get('engine_confidence', 'unknown')})"
            )
        if "objects" in report:
            lines.extend([
                f"Containers: {report.get('inspected_containers', 0)}",
                f"Objects: {len(report.get('objects', []))}",
            ])
        adapter = report.get("adapter")
        if adapter:
            summary = adapter["summary"]
            lines.append(
                f"Adapter: {adapter['adapter']} "
                f"({adapter['detection']['status']})"
            )
            lines.append(
                f"Songs: {summary['song_count']}, "
                f"charts: {summary['associated_chart_count']} associated / "
                f"{summary['unassociated_chart_count']} unassociated"
            )
        export = report.get("resource_export")
        if export:
            lines.append(
                f"Export: {export['exported_object_count']} exported, "
                f"{export['failed_object_count']} failed"
            )
            if "missing_plan_resource_count" in export:
                lines.append(
                    f"Missing plan resources: {export['missing_plan_resource_count']}"
                )
        lines.append(f"Warnings: {len(report.get('warnings', []))}")
        lines.append(f"Errors: {len(report.get('errors', []))}")
        return lines

    @staticmethod
    def object_detail(obj: dict[str, Any]) -> str:
        return json.dumps(obj, ensure_ascii=False, indent=2)

    @staticmethod
    def report_text(report: dict[str, Any]) -> str:
        return json.dumps(report, ensure_ascii=False, indent=2)
