"""Canonical, JSON-safe models for P1 object enumeration and references."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class ObjectRecord:
    """One Unity object, identified by its source container and Path ID."""

    resource_id: str
    source_id: str
    container_id: str
    path_id: int
    type_name: str
    name: str | None = None
    size_bytes: int | None = None
    container_path: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    decode_status: str = "metadata_only"
    warnings: list[str] = field(default_factory=list)


@dataclass
class ObjectReference:
    """A serialized PPtr edge; unresolved pointers retain their raw identifiers."""

    source_resource_id: str
    target_resource_id: str | None
    target_container_id: str | None
    target_path_id: int
    file_id: int
    resolved: bool


@dataclass
class EnumerationReport:
    schema_version: str
    input_path: str
    scan_status: str = "completed"
    parser_name: str = "UnityPy"
    parser_version: str | None = None
    inspected_containers: int = 0
    objects: list[ObjectRecord] = field(default_factory=list)
    references: list[ObjectReference] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
