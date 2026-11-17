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
        """Yield PPtr edges from UnityPy's typed object representation.

        parse_as_dict() converts PPtr instances into ordinary dictionaries,
        losing their dereference behavior. parse_as_object() preserves typed
        PPtr values, which are required for reliable target resolution.
        Primitive arrays (for example Font.m_FontData) are intentionally
        skipped rather than traversed element by element.
        """
        root = obj.parse_as_object()
        stack: list[tuple[Any, int]] = [(root, 0)]
        visited_nodes = 0
        visited_objects: set[int] = set()
        seen_pointers: set[tuple[int, int]] = set()

        while stack:
            value, depth = stack.pop()
            if value is None or isinstance(value, (str, bytes, bytearray, int, float, bool)):
                continue

            value_identity = id(value)
            if value_identity in visited_objects:
                continue
            visited_objects.add(value_identity)

            visited_nodes += 1
            if visited_nodes > MAX_REFERENCE_NODES_PER_OBJECT:
                raise ValueError("Reference traversal node limit reached.")
            if depth > MAX_REFERENCE_DEPTH:
                raise ValueError("Reference traversal depth limit reached.")

            if type(value).__name__ == "PPtr" and hasattr(value, "m_FileID") and hasattr(value, "m_PathID"):
                file_id = int(value.m_FileID)
                path_id = int(value.m_PathID)
                key = (file_id, path_id)
                if path_id == 0 or key in seen_pointers:
                    continue
                seen_pointers.add(key)
                try:
                    target = value.deref()
                    assets_file = getattr(target, "assets_file", None)
                    target_container = getattr(assets_file, "name", None)
                    yield file_id, path_id, str(target_container) if target_container else None, True
                except Exception:
                    yield file_id, path_id, None, False
                continue

            if isinstance(value, dict):
                children = value.values()
            elif isinstance(value, (list, tuple, set)):
                # Avoid spending the traversal budget on large primitive arrays.
                children = (
                    child for child in value
                    if child is not None and not isinstance(child, (str, bytes, bytearray, int, float, bool))
                )
            else:
                attrs = getattr(type(value), "__attrs_attrs__", None)
                if attrs:
                    children = (
                        getattr(value, item.name)
                        for item in attrs
                        if hasattr(value, item.name)
                    )
                elif hasattr(value, "__dict__"):
                    children = vars(value).values()
                else:
                    slots = getattr(type(value), "__slots__", ())
                    if isinstance(slots, str):
                        slots = (slots,)
                    children = (
                        getattr(value, name)
                        for name in slots
                        if hasattr(value, name) and not name.startswith("_")
                    )
            stack.extend((child, depth + 1) for child in children)
