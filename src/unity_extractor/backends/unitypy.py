"""UnityPy adapter. No UnityPy imports occur until the backend is instantiated."""
from __future__ import annotations

from pathlib import Path
from typing import Any


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
