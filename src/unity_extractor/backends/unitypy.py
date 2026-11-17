"""UnityPy adapter. No UnityPy imports occur until the backend is instantiated."""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any


MAX_REFERENCE_NODES_PER_OBJECT = 100_000
MAX_REFERENCE_DEPTH = 128


class UnityPyBackend:
    name = "UnityPy"

    def __init__(self) -> None:
        try:
            import UnityPy
        except ImportError as exc:
            raise RuntimeError(
                'Object enumeration requires the optional dependency. '
                'Install it with: python -m pip install -e ".[unity]"'
            ) from exc
        self._module = UnityPy
        self.version = getattr(UnityPy, "__version__", None)

    def load(self, source: Path) -> Any:
        return self._module.load(str(source))

    def iter_references(self, obj: Any) -> Iterator[tuple[int, int, str | None, bool]]:
        """Parse one object's typetree and yield serialized PPtr targets.

        Payloads are parsed only as structured metadata. No texture, audio,
        mesh, or other resource export/decode operation is performed.
        """
        tree = obj.parse_as_dict()
        stack: list[tuple[Any, int]] = [(tree, 0)]
        visited_nodes = 0
        seen: set[tuple[int, int]] = set()

        while stack:
            value, depth = stack.pop()
            visited_nodes += 1
            if visited_nodes > MAX_REFERENCE_NODES_PER_OBJECT:
                raise ValueError("Reference traversal node limit reached.")
            if depth > MAX_REFERENCE_DEPTH:
                raise ValueError("Reference traversal depth limit reached.")

            value_type = type(value)
            if value_type.__name__ == "PPtr" and hasattr(value, "m_FileID") and hasattr(value, "m_PathID"):
                file_id = int(value.m_FileID)
                path_id = int(value.m_PathID)
                key = (file_id, path_id)
                if key in seen or path_id == 0:
                    continue
                seen.add(key)
                try:
                    target = value.deref()
                    assets_file = getattr(target, "assets_file", None)
                    target_container = getattr(assets_file, "name", None)
                    yield file_id, path_id, str(target_container) if target_container else None, True
                except Exception:
                    yield file_id, path_id, None, False
                continue

            if isinstance(value, dict):
                stack.extend((child, depth + 1) for child in value.values())
            elif isinstance(value, (list, tuple)):
                stack.extend((child, depth + 1) for child in value)
