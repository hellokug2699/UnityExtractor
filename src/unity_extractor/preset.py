"""Load and validate adapter export plans before writing any file."""
from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
from typing import Any

MAX_PLAN_BYTES = 16 * 1024 * 1024
MAX_PLAN_ENTRIES = 200_000


def _preset_entries(data: Any) -> list[Any]:
    if not isinstance(data, dict):
        raise ValueError("Export plan root is not a JSON object.")
    preset = data.get("export_preset")
    if preset is None:
        adapter = data.get("adapter")
        if isinstance(adapter, dict):
            preset = adapter.get("export_preset")
    if preset is None:
        preset = data
    if not isinstance(preset, dict):
        raise ValueError("Export plan does not contain an export_preset object.")
    entries = preset.get("entries")
    if not isinstance(entries, list):
        raise ValueError("Export plan does not contain an entries list.")
    if len(entries) > MAX_PLAN_ENTRIES:
        raise ValueError(f"Export plan exceeds {MAX_PLAN_ENTRIES} entries.")
    return entries


def load_plan_destinations(path: str | Path) -> dict[str, str]:
    """Return resource_id -> validated relative destination."""
    plan_path = Path(path).expanduser()
    if not plan_path.is_file():
        raise ValueError(f"Export plan does not exist: {plan_path}")
    if plan_path.stat().st_size > MAX_PLAN_BYTES:
        raise ValueError(f"Export plan exceeds {MAX_PLAN_BYTES} bytes.")
    try:
        data = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not read export plan: {exc}") from exc

    destinations: dict[str, str] = {}
    used_paths: set[str] = set()
    for index, entry in enumerate(_preset_entries(data)):
        if not isinstance(entry, dict):
            raise ValueError(f"Export plan entry {index} is not an object.")
        resource_id = entry.get("resource_id")
        destination = entry.get("destination")
        if not isinstance(resource_id, str) or not resource_id:
            raise ValueError(f"Export plan entry {index} has no resource_id.")
        if not isinstance(destination, str) or not destination:
            raise ValueError(f"Export plan entry {index} has no destination.")

        relative = PurePosixPath(destination.replace("\\", "/"))
        if relative.is_absolute() or ".." in relative.parts or not relative.parts:
            raise ValueError(
                f"Unsafe export destination for {resource_id!r}: {destination!r}"
            )
        normalized = relative.as_posix()
        previous = destinations.get(resource_id)
        if previous is not None and previous != normalized:
            raise ValueError(f"Conflicting destinations for resource {resource_id!r}.")
        if normalized in used_paths:
            raise ValueError(f"Duplicate export destination: {normalized!r}")
        destinations[resource_id] = normalized
        used_paths.add(normalized)
    return destinations
