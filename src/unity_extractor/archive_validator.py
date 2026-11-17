"""Streamingly verify a resource-export ZIP against its export manifest."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any


def _safe_archive_path(value: str) -> bool:
    if not value or "\\" in value:
        return False
    path = PurePosixPath(value)
    return bool(path.parts) and path.as_posix() == value and not path.is_absolute() and all(
        part not in ("", ".", "..") for part in path.parts
    )


def verify_export_archive(archive_path: str | Path, manifest_path: str | Path) -> dict[str, Any]:
    """Compare every archived artifact with the external manifest without extracting it."""
    archive_path = Path(archive_path).expanduser()
    manifest_path = Path(manifest_path).expanduser()
    result: dict[str, Any] = {
        "schema_version": "1.0", "verifier": "UnityExtractor", "status": "failed",
        "archive": str(archive_path), "manifest": str(manifest_path),
        "checked_file_count": 0, "checked_bytes": 0, "errors": [], "files": [],
    }
    try:
        manifest_bytes = manifest_path.read_bytes()
        manifest = json.loads(manifest_bytes)
    except (OSError, json.JSONDecodeError) as exc:
        result["errors"].append(f"Cannot read manifest: {type(exc).__name__}: {exc}")
        return result
    if not isinstance(manifest, dict) or not isinstance(manifest.get("objects"), list):
        result["errors"].append("Manifest must be an object containing an objects array.")
        return result

    expected: dict[str, tuple[int, str]] = {}
    failed_objects = 0
    for index, item in enumerate(manifest["objects"]):
        if not isinstance(item, dict):
            result["errors"].append(f"Manifest object #{index} is not an object.")
            continue
        if item.get("status") != "exported":
            failed_objects += 1
            result["errors"].append(f"Manifest object #{index} is not exported (status={item.get('status')!r}).")
        artifacts = item.get("artifacts", [])
        if not isinstance(artifacts, list):
            result["errors"].append(f"Manifest object #{index} has an invalid artifacts list.")
            continue
        if item.get("status") == "exported" and not artifacts:
            result["errors"].append(f"Exported manifest object #{index} has no artifacts.")
        for artifact in artifacts:
            if not isinstance(artifact, dict):
                result["errors"].append(f"Manifest object #{index} contains an invalid artifact.")
                continue
            path, size, digest = artifact.get("path"), artifact.get("size_bytes"), artifact.get("sha256")
            if not isinstance(path, str) or not _safe_archive_path(path):
                result["errors"].append(f"Unsafe or invalid artifact path: {path!r}")
                continue
            if path == "export-manifest.json":
                result["errors"].append("Artifact path conflicts with export-manifest.json.")
                continue
            if path in expected:
                result["errors"].append(f"Duplicate artifact path in manifest: {path}")
                continue
            if not isinstance(size, int) or size < 0:
                result["errors"].append(f"Invalid size for artifact: {path}")
                continue
            if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
                result["errors"].append(f"Invalid SHA-256 for artifact: {path}")
                continue
            expected[path] = (size, digest.lower())

    if manifest.get("export_status") != "completed":
        result["errors"].append(f"Manifest export_status is not completed: {manifest.get('export_status')!r}.")
    if failed_objects:
        result["failed_object_count"] = failed_objects

    try:
        with zipfile.ZipFile(archive_path, "r") as archive:
            members: dict[str, zipfile.ZipInfo] = {}
            for info in archive.infolist():
                name = info.filename.rstrip("/") if info.is_dir() else info.filename
                if not _safe_archive_path(name):
                    result["errors"].append(f"Unsafe path in ZIP archive: {info.filename!r}")
                    continue
                if info.is_dir():
                    continue
                if name in members:
                    result["errors"].append(f"Duplicate file entry in ZIP archive: {name}")
                    continue
                members[name] = info

            archived_manifest = members.get("export-manifest.json")
            if archived_manifest is None:
                result["errors"].append("ZIP does not contain root-level export-manifest.json.")
            else:
                with archive.open(archived_manifest, "r") as stream:
                    if stream.read() != manifest_bytes:
                        result["errors"].append("Archived export-manifest.json differs from supplied manifest.")

            for path, (expected_size, expected_hash) in expected.items():
                info = members.get(path)
                if info is None:
                    result["errors"].append(f"Missing artifact in ZIP: {path}")
                    continue
                if info.file_size != expected_size:
                    result["errors"].append(f"Size mismatch for {path}: manifest={expected_size}, zip={info.file_size}.")
                    continue
                digest = hashlib.sha256()
                actual_size = 0
                with archive.open(info, "r") as stream:
                    while True:
                        chunk = stream.read(1024 * 1024)
                        if not chunk:
                            break
                        actual_size += len(chunk)
                        digest.update(chunk)
                actual_hash = digest.hexdigest()
                result["checked_file_count"] += 1
                result["checked_bytes"] += actual_size
                passed = actual_size == expected_size and actual_hash == expected_hash
                result["files"].append({
                    "path": path, "size_bytes": actual_size, "sha256": actual_hash,
                    "status": "passed" if passed else "failed",
                })
                if not passed:
                    result["errors"].append(f"SHA-256 or streamed size mismatch for {path}.")

            allowed = set(expected) | {"export-manifest.json"}
            extras = sorted(set(members) - allowed)
            if extras:
                result["extra_files"] = extras
                result["errors"].append(f"ZIP contains {len(extras)} unexpected file(s).")
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        result["errors"].append(f"Cannot verify ZIP: {type(exc).__name__}: {exc}")

    result["expected_file_count"] = len(expected)
    result["status"] = "passed" if not result["errors"] else "failed"
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="unity-extractor-verify-archive",
        description="Streamingly verify a UnityExtractor ZIP against export-manifest.json.",
    )
    parser.add_argument("archive", help="ZIP archive with paths relative to the export directory")
    parser.add_argument("--manifest", required=True, help="External export-manifest.json")
    parser.add_argument("--output", help="Write the JSON verification report to this path")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = verify_export_archive(args.archive, args.manifest)
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        try:
            Path(args.output).expanduser().write_text(rendered, encoding="utf-8")
        except OSError as exc:
            print(f"Could not write verification report: {exc}", file=sys.stderr)
            return 2
    print(rendered, end="")
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
