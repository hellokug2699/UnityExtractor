"""Project-owned adapter contracts.

Adapters consume canonical enumeration records and never become a
prerequisite for generic scanning or export. Core modules must not import
a concrete adapter.
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from ..object_models import EnumerationReport, ObjectRecord


@runtime_checkable
class GameAdapter(Protocol):
    """Optional semantic layer over a completed enumeration report."""

    name: str

    def __call__(self, obj: Any, record: ObjectRecord) -> None:
        """Observe one object while its source container is still loaded."""

    def analyze(self, report: EnumerationReport) -> dict[str, Any]:
        """Return a JSON-safe adapter section for the enumeration report."""


class CompositeCallback:
    """Run several object callbacks without coupling them to each other."""

    def __init__(self, *callbacks: Any) -> None:
        self.callbacks = callbacks

    def __call__(self, obj: Any, record: ObjectRecord) -> None:
        for callback in self.callbacks:
            callback(obj, record)
