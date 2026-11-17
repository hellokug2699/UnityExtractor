"""Canonical, JSON-safe models for P1 object enumeration and references."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from typing import Any

from .resource_classifier import classify_unity_type


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
    resource_category: str = "unknown"
    resource_subcategory: str = "unknown"
    classification_confidence: str = "unknown"
    classification_basis: str = "unrecognized_unity_type"

    def __post_init__(self) -> None:
        classification = classify_unity_type(self.type_name)
        self.resource_category = classification.category
        self.resource_subcategory = classification.subcategory
        self.classification_confidence = classification.confidence
        self.classification_basis = classification.basis


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
    addressables: dict[str, Any] = field(default_factory=dict)
    # Full catalog key rows stay in memory for adapters and are not serialized
    # into the scan report, which only contains bounded samples.
    catalog_key_entries: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        # Avoid deep-copying potentially large in-memory adapter rows.
        data = asdict(replace(self, catalog_key_entries=[]))
        data.pop("catalog_key_entries", None)
        return data
