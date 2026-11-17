"""Command-line interface."""
from __future__ import annotations

import argparse
import json
import sys

from . import __version__
from .scanner import UnityScanner


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="unity-extractor",
        description="Inspect a Unity game directory, APK/ZIP, or standalone resource file.",
    )
    parser.add_argument("input", help="Path to a game directory, APK, ZIP, or resource file")
    parser.add_argument("--format", choices=("text", "json"), default="text", help="Report format")
    parser.add_argument("--output", help="Write the report to this file instead of stdout")
    parser.add_argument("--enumerate-objects", action="store_true", help="Enumerate Unity objects using the optional UnityPy backend")
    parser.add_argument("--analyze-references", action="store_true", help="Parse serialized object data and enumerate PPtr references (requires --enumerate-objects)")
    parser.add_argument("--analyze-dependencies", action="store_true", help="Build a dependency graph from object references (requires --analyze-references)")
    parser.add_argument("--dependency-root", help="Resource ID used to report its transitive dependency chain")
    parser.add_argument("--dependency-depth", type=int, default=8, help="Maximum dependency-chain depth (default: 8)")
    parser.add_argument("--max-container-mb", type=int, default=512, help="Maximum size of one candidate container in MiB (default: 512)")
    parser.add_argument("--export-resources", action="store_true", help="Export supported Unity resources while enumerating objects")
    parser.add_argument("--export-dir", help="Directory for exported resources and export-manifest.json (requires --export-resources)")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def render_text(report: dict) -> str:
    lines = [
        f"Input: {report['input_path']}",
        f"Kind: {report['input_kind']}",
        f"Status: {report['scan_status']}",
        f"Engine: {report['engine_name']} (confidence: {report['engine_confidence']})",
        f"Unity version: {report['unity_version'] or 'unknown'} (confidence: {report['unity_version_confidence']})",
        f"Scripting backend: {report['scripting_backend']}",
        f"Inspected entries: {report['inspected_entries']}",
        "",
        "Findings:",
    ]
    if report["findings"]:
        for finding in report["findings"]:
            lines.append(f"  - {finding['kind']}: {finding['status']} ({finding['count']} evidence item(s))")
            for evidence in finding["evidence"]:
                source = f" [{evidence['source']}]" if evidence["source"] else ""
                lines.append(f"      {evidence['description']}{source}")
    else:
        lines.append("  (none)")
    if report["warnings"]:
        lines.extend(["", "Warnings:", *[f"  - {item}" for item in report["warnings"]]])
    if report["errors"]:
        lines.extend(["", "Errors:", *[f"  - {item}" for item in report["errors"]]])
    return "\n".join(lines)


def render_object_text(report: dict) -> str:
    lines = [
        f"Input: {report['input_path']}",
        f"Status: {report['scan_status']}",
        f"Parser: {report['parser_name']} ({report['parser_version'] or 'version unknown'})",
        f"Inspected containers: {report['inspected_containers']}",
        f"Objects: {len(report['objects'])}",
        "",
        "Objects:",
    ]
    for obj in report["objects"]:
        lines.append(
            f"  - {obj['type_name']} [{obj['resource_category']}/{obj['resource_subcategory']}] | {obj['name'] or '<unnamed>'} | "
            f"Path ID: {obj['path_id']} | Size: {obj['size_bytes'] if obj['size_bytes'] is not None else 'unknown'}"
        )
        lines.append(f"      Container: {obj['container_id']}")
        if obj["container_path"]:
            lines.append(f"      Asset path: {obj['container_path']}")
        if obj["warnings"]:
            lines.extend(f"      Warning: {warning}" for warning in obj["warnings"])
    export = report.get("resource_export")
    if export:
        lines.extend([
            "",
            "Resource export:",
            f"  Supported types: {', '.join(export['supported_types'])}",
            f"  Exported objects: {export['exported_object_count']}",
            f"  Failed objects: {export['failed_object_count']}",
            f"  Manifest: {export['manifest_path']}",
        ])
    addressables = report.get("addressables")
    if addressables:
        summary = addressables["summary"]
        lines.extend([
            "",
            "Addressables:",
            f"  Catalog candidates: {summary['catalog_candidate_count']}",
            f"  Validated catalogs: {summary['validated_catalog_count']}",
            f"  Internal IDs: {summary['internal_id_count']}",
            f"  Matched internal IDs: {summary['matched_internal_id_count']}",
            f"  Unresolved internal IDs: {summary['unresolved_internal_id_count']}",
            f"  Key mapping: {summary['key_mapping_status']}",
        ])
    graph = report.get("dependency_graph")
    if graph:
        summary = graph["summary"]
        lines.extend([
            "",
            "Dependency graph:",
            f"  Nodes: {summary['node_count']}",
            f"  Edges: {summary['edge_count']}",
            f"  Unresolved references: {summary['unresolved_reference_count']}",
            f"  Dangling references: {summary['dangling_reference_count']}",
            f"  Unreferenced objects: {summary['unreferenced_object_count']}",
            f"  Isolated objects: {summary['isolated_object_count']}",
            f"  Cycles: {summary['cycle_count']}",
        ])
        if graph["dependency_chain_error"]:
            lines.append(f"  Dependency chain: {graph['dependency_chain_error']}")
        elif graph["dependency_chain_root"]:
            lines.append("  Dependency chain (shortest paths):")
            for item in graph["dependency_chains"]:
                lines.append(f"    - depth {item['depth']}: {' -> '.join(item['path'])}")
            if graph["dependency_chains_truncated"]:
                lines.append("    (chain output truncated by node limit)")
    if report["warnings"]:
        lines.extend(["", "Warnings:", *[f"  - {item}" for item in report["warnings"]]])
    if report["errors"]:
        lines.extend(["", "Errors:", *[f"  - {item}" for item in report["errors"]]])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.export_resources and not args.enumerate_objects:
        print("--export-resources requires --enumerate-objects.", file=sys.stderr)
        return 2
    if args.export_resources and not args.export_dir:
        print("--export-resources requires --export-dir.", file=sys.stderr)
        return 2
    if args.export_dir and not args.export_resources:
        print("--export-dir requires --export-resources.", file=sys.stderr)
        return 2
    if args.analyze_references and not args.enumerate_objects:
        print("--analyze-references requires --enumerate-objects.", file=sys.stderr)
        return 2
    if args.analyze_dependencies and not args.analyze_references:
        print("--analyze-dependencies requires --analyze-references.", file=sys.stderr)
        return 2
    if args.dependency_root and not args.analyze_dependencies:
        print("--dependency-root requires --analyze-dependencies.", file=sys.stderr)
        return 2
    if args.dependency_depth < 0:
        print("--dependency-depth must be zero or greater.", file=sys.stderr)
        return 2
    if args.max_container_mb < 1:
        print("--max-container-mb must be at least 1.", file=sys.stderr)
        return 2

    if args.enumerate_objects:
        from .enumerator import UnityObjectEnumerator

        exporter = None
        if args.export_resources:
            from .resource_exporter import ResourceExporter
            exporter = ResourceExporter(args.export_dir)
        try:
            enumerator = UnityObjectEnumerator(
                max_container_bytes=args.max_container_mb * 1024 * 1024,
                include_references=args.analyze_references,
                object_callback=exporter,
            )
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        report = enumerator.enumerate(args.input).to_dict()
        if exporter is not None:
            try:
                manifest_path = exporter.write_manifest()
                report["resource_export"] = exporter.summary() | {"manifest_path": str(manifest_path)}
            except OSError as exc:
                report.setdefault("errors", []).append(f"Could not write export manifest: {exc}")
                report["scan_status"] = "partial" if report.get("objects") else "failed"
        if args.analyze_dependencies:
            from .dependency_graph import build_dependency_graph

            report["dependency_graph"] = build_dependency_graph(
                report["objects"],
                report["references"],
                root_resource_id=args.dependency_root,
                max_depth=args.dependency_depth,
            )
        rendered = json.dumps(report, ensure_ascii=False, indent=2) if args.format == "json" else render_object_text(report)
    else:
        report = UnityScanner().scan(args.input).to_dict()
        rendered = json.dumps(report, ensure_ascii=False, indent=2) if args.format == "json" else render_text(report)
    if args.output:
        try:
            with open(args.output, "w", encoding="utf-8") as stream:
                stream.write(rendered + "\n")
        except OSError as exc:
            print(f"Could not write report: {exc}", file=sys.stderr)
            return 2
    else:
        print(rendered)
    export = report.get("resource_export")
    export_failed = bool(export and export.get("failed_object_count", 0))
    return 0 if report["scan_status"] == "completed" and not export_failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
