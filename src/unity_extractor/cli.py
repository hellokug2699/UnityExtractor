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


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
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
