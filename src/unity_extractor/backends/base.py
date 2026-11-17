"""Project-owned boundary for Unity container parsers."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol


class UnityParserBackend(Protocol):
    """Minimal interface required by the object enumerator."""

    name: str
    version: str | None

    def load(self, source: Path) -> Any:
        """Load one standalone SerializedFile or Unity container."""
        ...
