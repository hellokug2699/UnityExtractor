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
            f"  - {obj['type_name']} | {obj['name'] or '<unnamed>'} | "
            f"Path ID: {obj['path_id']} | Size: {obj['size_bytes'] if obj['size_bytes'] is not None else 'unknown'}"
        )
        lines.append(f"      Container: {obj['container_id']}")
        if obj["container_path"]:
            lines.append(f"      Asset path: {obj['container_path']}")
        if obj["warnings"]:
            lines.extend(f"      Warning: {warning}" for warning in obj["warnings"])
    if report["warnings"]:
        lines.extend(["", "Warnings:", *[f"  - {item}" for item in report["warnings"]]])
    if report["errors"]:
        lines.extend(["", "Errors:", *[f"  - {item}" for item in report["errors"]]])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
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

        try:
            enumerator = UnityObjectEnumerator(
                max_container_bytes=args.max_container_mb * 1024 * 1024,
                include_references=args.analyze_references
            )
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        report = enumerator.enumerate(args.input).to_dict()
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
    return 0 if report["scan_status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
