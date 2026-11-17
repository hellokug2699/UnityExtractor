"""Safe, manifest-backed export of supported Unity resources."""
from __future__ import annotations

import hashlib
import json
import platform
import re
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

from .object_models import ObjectRecord

MAX_IMAGE_PIXELS = 100_000_000
HASH_CHUNK_SIZE = 1024 * 1024


def _prepare_unitypy_export_import() -> None:
    """Keep UnityPy image exporters usable when FMOD is unavailable on Android.

    UnityPy eagerly imports every exporter from UnityPy.export. Its audio
    converter imports fmod_toolkit at module load time, which currently aborts
    on Android even when the caller only needs image conversion. A narrow
    placeholder lets non-audio exporters load; unsupported FMOD conversion
    still fails explicitly if it is ever requested.
    """
    if platform.system() != "Android" or "fmod_toolkit" in sys.modules:
        return

    fmod_stub = ModuleType("fmod_toolkit")

    def _unsupported_audio_conversion(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError(
            "FMOD audio conversion is unavailable on Android; "
            "image and non-FMOD audio exports remain available."
        )

    fmod_stub.raw_to_wav = _unsupported_audio_conversion  # type: ignore[attr-defined]
    sys.modules["fmod_toolkit"] = fmod_stub


def _safe_stem(value: str | None) -> str:
    """Return a bounded filename stem; never interpret asset names as paths."""
    candidate = re.sub(r"[^A-Za-z0-9._-]+", "_", value or "").strip("._-")
    candidate = candidate[:80].rstrip("._-")
    return candidate or "unnamed"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(HASH_CHUNK_SIZE)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _enum_name(value: Any) -> str | None:
    if value is None:
        return None
    name = getattr(value, "name", None)
    return str(name if name is not None else value)


class ResourceExporter:
    """Export supported objects while their source container is still loaded.

    TextAsset payloads are preserved byte-for-byte. Texture2D and Sprite
    objects are decoded by UnityPy and written as PNG images.
    """

    def __init__(self, output_dir: str | Path) -> None:
        self.output_dir = Path(output_dir).expanduser()
        self.records: list[dict[str, Any]] = []

    def __call__(self, obj: Any, record: ObjectRecord) -> None:
        self.export_object(obj, record)

    def export_object(self, obj: Any, record: ObjectRecord) -> dict[str, Any] | None:
        if record.type_name not in {"TextAsset", "Texture2D", "Sprite"}:
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
            if record.type_name == "TextAsset":
                self._export_text_asset(parsed, record, entry)
            else:
                self._export_image(parsed, record, entry)
        except Exception as exc:
            entry["warnings"].append(f"{type(exc).__name__}: {exc}")

        self.records.append(entry)
        return entry

    def _export_text_asset(
        self, parsed: Any, record: ObjectRecord, entry: dict[str, Any]
    ) -> None:
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

    def _export_image(
        self, parsed: Any, record: ObjectRecord, entry: dict[str, Any]
    ) -> None:
        _prepare_unitypy_export_import()
        image = getattr(parsed, "image")
        size = getattr(image, "size", None)
        if (
            not isinstance(size, (tuple, list))
            or len(size) != 2
            or not all(isinstance(value, int) for value in size)
        ):
            raise ValueError("Decoded image does not expose a valid width and height.")
        width, height = size
        if width <= 0 or height <= 0:
            raise ValueError(f"Decoded image has invalid dimensions: {width}x{height}.")
        if width * height > MAX_IMAGE_PIXELS:
            raise ValueError(
                f"Decoded image exceeds the {MAX_IMAGE_PIXELS}-pixel safety limit: "
                f"{width}x{height}."
            )

        folder = "texture2d" if record.type_name == "Texture2D" else "sprite"
        relative = Path("images") / folder / (
            f"{_safe_stem(record.name)}__{record.path_id}_{record.resource_id[:10]}.png"
        )
        destination = self.output_dir / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(destination.name + ".tmp")
        try:
            # Specify the format explicitly because the temporary filename ends in .tmp.
            image.save(str(temporary), format="PNG")
            if not temporary.is_file() or temporary.stat().st_size <= 0:
                raise ValueError("Image encoder produced an empty output.")
            size_bytes = temporary.stat().st_size
            sha256 = _sha256_file(temporary)
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)

        source_format = _enum_name(getattr(parsed, "m_TextureFormat", None))
        entry["status"] = "exported"
        entry["metadata"] = {
            "image_width": width,
            "image_height": height,
            "image_mode": getattr(image, "mode", None),
            "output_format": "PNG",
            "source_texture_format": source_format,
            "source_width": getattr(parsed, "m_Width", None),
            "source_height": getattr(parsed, "m_Height", None),
        }
        entry["artifacts"].append({
            "path": relative.as_posix(),
            "size_bytes": size_bytes,
            "sha256": sha256,
            "format": "PNG",
        })

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
            "supported_types": ["TextAsset", "Texture2D", "Sprite"],
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
            "supported_types": ["TextAsset", "Texture2D", "Sprite"],
            "selected_object_count": len(self.records),
            "exported_object_count": sum(item["status"] == "exported" for item in self.records),
            "failed_object_count": sum(item["status"] == "failed" for item in self.records),
        }
