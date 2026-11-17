"""Safe, manifest-backed export of supported Unity resources."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .object_models import ObjectRecord


def _safe_stem(value: str | None) -> str:
    """Return a bounded filename stem; never interpret asset names as paths."""
    candidate = re.sub(r"[^A-Za-z0-9._-]+", "_", value or "").strip("._-")
    candidate = candidate[:80].rstrip("._-")
    return candidate or "unnamed"


class ResourceExporter:
    """Export supported objects while their source container is still loaded.

    P2.1 intentionally supports TextAsset only. Other P2 handlers are added in
    later steps after their payload behavior is covered by fixtures.
    """

    def __init__(self, output_dir: str | Path) -> None:
        self.output_dir = Path(output_dir).expanduser()
        self.records: list[dict[str, Any]] = []

    def __call__(self, obj: Any, record: ObjectRecord) -> None:
        self.export_object(obj, record)

    def export_object(self, obj: Any, record: ObjectRecord) -> dict[str, Any] | None:
        if record.type_name != "TextAsset":
            return None

        entry: dict[str, Any] = {
            "resource_id": record.resource_id,
            "source_id": record.source_id,
            "container_id": record.container_id,
            "path_id": record.path_id,
            "type_name": record.type_name,
            "name": record.name,
            "status": "failed",
            "artifacts": [],
            "warnings": [],
        }
        try:
            parsed = obj.parse_as_object()
            payload = getattr(parsed, "m_Script")
            if isinstance(payload, str):
                data = payload.encode("utf-8", errors="surrogateescape")
            elif isinstance(payload, (bytes, bytearray, memoryview)):
                data = bytes(payload)
            else:
                raise TypeError(f"Unsupported TextAsset payload type: {type(payload).__name__}")

            relative = Path("text") / (
                f"{_safe_stem(record.name)}__{record.path_id}_{record.resource_id[:10]}.txt"
            )
            destination = self.output_dir / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
            entry["status"] = "exported"
            entry["artifacts"].append({
                "path": relative.as_posix(),
                "size_bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "encoding": "raw_bytes",
            })
        except Exception as exc:
            entry["warnings"].append(f"{type(exc).__name__}: {exc}")

        self.records.append(entry)
        return entry

    def write_manifest(self) -> Path:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        manifest = {
            "schema_version": "1.0",
            "exporter": "UnityExtractor",
            "export_status": (
                "completed"
                if all(item["status"] == "exported" for item in self.records)
                else "partial"
            ),
            "supported_types": ["TextAsset"],
            "selected_object_count": len(self.records),
            "exported_object_count": sum(item["status"] == "exported" for item in self.records),
            "failed_object_count": sum(item["status"] == "failed" for item in self.records),
            "objects": self.records,
        }
        destination = self.output_dir / "export-manifest.json"
        temporary = destination.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(destination)
        return destination

    def summary(self) -> dict[str, Any]:
        return {
            "supported_types": ["TextAsset"],
            "selected_object_count": len(self.records),
            "exported_object_count": sum(item["status"] == "exported" for item in self.records),
            "failed_object_count": sum(item["status"] == "failed" for item in self.records),
        }
