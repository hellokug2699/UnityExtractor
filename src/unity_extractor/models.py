"""Typed report models for the P0 scanner."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Evidence:
    code: str
    description: str
    source: str | None = None
    strength: str = "supporting"


@dataclass
class Finding:
    kind: str
    status: str
    evidence: list[Evidence] = field(default_factory=list)
    count: int | None = None


@dataclass
class ScanReport:
    schema_version: str
    input_path: str
    input_kind: str
    scan_status: str = "completed"
    engine_name: str = "unknown"
    engine_confidence: str = "unknown"
    unity_version: str | None = None
    unity_version_confidence: str = "unknown"
    scripting_backend: str = "unknown"
    findings: list[Finding] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    inspected_entries: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
