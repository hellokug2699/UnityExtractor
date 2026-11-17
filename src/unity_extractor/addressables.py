"""Conservative Addressables catalog inspection.

Only JSON catalogs are validated. Binary catalogs and key/bucket mappings
are reported as unsupported rather than guessed. Internal IDs are matched
to discovered containers by filename so unresolved entries stay visible.
"""
from __future__ import annotations

import json
import re
from pathlib import PurePosixPath
from typing import Any, Iterable

MAX_CATALOG_BYTES = 8 * 1024 * 1024
MAX_CATALOGS = 16
MAX_CATALOG_TOTAL_BYTES = 32 * 1024 * 1024
MAX_INTERNAL_IDS = 200_000
MAX_ID_LENGTH = 4096
MAX_PLACEHOLDERS = 16
MAX_UNRESOLVED_SAMPLES = 20
MAX_WARNING_VALUE_LENGTH = 200
MAX_CATALOG_WARNINGS = 50

CATALOG_SUFFIXES = {".json", ".bin", ".bytes"}
PLACEHOLDER_RE = re.compile(r"\{[^{}]+\}")


def is_catalog_candidate_name(name: str) -> bool:
    """Return True for catalog-like filenames, independent of game name."""
    base = name.replace("\\", "/").rsplit("/", 1)[-1].lower()
    return PurePosixPath(base).suffix in CATALOG_SUFFIXES and "catalog" in base


def _bounded_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    return value[:MAX_ID_LENGTH]


def _add_warning(entry: dict[str, Any], message: str) -> None:
    if len(entry["warnings"]) < MAX_CATALOG_WARNINGS:
        entry["warnings"].append(message)


def inspect_catalog(
    source_id: str, name: str, data: bytes
) -> tuple[dict[str, Any], list[str]]:
    """Validate one catalog candidate and extract location-level metadata.

    The returned entry is JSON-safe. Internal IDs are returned separately so
    they can be matched against enumerated containers without bloating the
    final report with every location.
    """
    entry: dict[str, Any] = {
        "source_id": source_id,
        "name": name,
        "status": "failed",
        "format": None,
        "locator_id": None,
        "build_result_hash": None,
        "internal_id_count": 0,
        "provider_id_count": 0,
        "runtime_path_placeholders": [],
        "key_mapping_status": "absent",
        "warnings": [],
    }
    internal_ids: list[str] = []

    if not data:
        _add_warning(entry, "Catalog candidate is empty.")
        return entry, internal_ids
    if len(data) > MAX_CATALOG_BYTES:
        _add_warning(
            entry, f"Catalog candidate exceeds the {MAX_CATALOG_BYTES}-byte limit."
        )
        return entry, internal_ids

    stripped = data.lstrip()
    if not stripped.startswith(b"{"):
        entry["status"] = "unsupported_binary"
        _add_warning(
            entry, "Catalog candidate is not JSON; binary catalogs are not parsed."
        )
        return entry, internal_ids

    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        _add_warning(entry, f"Catalog is not valid UTF-8: {exc}")
        return entry, internal_ids

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        _add_warning(entry, f"Catalog JSON is invalid: {exc}")
        return entry, internal_ids
    if not isinstance(payload, dict):
        _add_warning(entry, "Catalog JSON root is not an object.")
        return entry, internal_ids

    locator = payload.get("m_LocatorId")
    raw_internal = payload.get("m_InternalIds")
    raw_providers = payload.get("m_ProviderIds")
    if not (
        isinstance(locator, str)
        or isinstance(raw_internal, list)
        or isinstance(raw_providers, list)
    ):
        entry["status"] = "candidate_unvalidated"
        _add_warning(
            entry,
            "Catalog markers (m_LocatorId / m_InternalIds / m_ProviderIds) were not found.",
        )
        return entry, internal_ids

    entry["status"] = "parsed"
    entry["format"] = "json"
    entry["locator_id"] = _bounded_text(locator)
    entry["build_result_hash"] = _bounded_text(payload.get("m_BuildResultHash"))

    if not isinstance(raw_internal, list):
        _add_warning(entry, "m_InternalIds is missing or is not a list.")
        raw_internal = []
    elif len(raw_internal) > MAX_INTERNAL_IDS:
        _add_warning(
            entry, f"Internal ID list truncated at {MAX_INTERNAL_IDS} entries."
        )
        raw_internal = raw_internal[:MAX_INTERNAL_IDS]
    for value in raw_internal:
        bounded = _bounded_text(value)
        if bounded is None:
            _add_warning(
                entry, f"Skipped non-string internal ID: {type(value).__name__}."
            )
            continue
        internal_ids.append(bounded)

    if not isinstance(raw_providers, list):
        _add_warning(entry, "m_ProviderIds is missing or is not a list.")
        provider_count = 0
    else:
        provider_count = len(raw_providers)

    entry["internal_id_count"] = len(internal_ids)
    entry["provider_id_count"] = provider_count

    placeholders: list[str] = []
    for identifier in internal_ids:
        for placeholder in PLACEHOLDER_RE.findall(identifier):
            if placeholder not in placeholders:
                placeholders.append(placeholder)
                if len(placeholders) >= MAX_PLACEHOLDERS:
                    break
        if len(placeholders) >= MAX_PLACEHOLDERS:
            break
    entry["runtime_path_placeholders"] = placeholders

    key_data = payload.get("m_KeyDataString")
    if isinstance(key_data, str) and key_data:
        entry["key_mapping_status"] = "unsupported"
        _add_warning(
            entry, "Key/bucket mapping is not decoded; catalog keys remain unresolved."
        )
    elif key_data is not None:
        _add_warning(entry, "m_KeyDataString has an unsupported type.")

    return entry, internal_ids


def _identifier_basename(identifier: str) -> str:
    path = identifier.replace("\\", "/").split("?", 1)[0].rstrip("/")
    return path.rsplit("/", 1)[-1].lower()


def _source_basename(source_id: str) -> str:
    path = source_id.replace("\\", "/").rstrip("/")
    return path.rsplit("/", 1)[-1].lower()


def _display_identifier(identifier: str) -> str:
    if len(identifier) <= MAX_WARNING_VALUE_LENGTH:
        return identifier
    return identifier[:MAX_WARNING_VALUE_LENGTH] + "..."


def build_addressables_report(
    catalogs: Iterable[tuple[dict[str, Any], list[str]]],
    source_ids: Iterable[str],
) -> dict[str, Any]:
    """Attach container-match counts and a bounded global summary.

    Matching is filename-based on purpose: only relationships the scan can
    actually observe are reported. Unmatched internal IDs remain counted and
    sampled instead of being silently discarded.
    """
    basenames = {_source_basename(value) for value in source_ids if value}
    basenames.discard("")

    entries: list[dict[str, Any]] = []
    internal_total = 0
    matched_total = 0
    unresolved_total = 0
    unresolved_samples: list[str] = []
    validated_total = 0

    for entry, internal_ids in catalogs:
        matched = 0
        unresolved = 0
        for identifier in internal_ids:
            if _identifier_basename(identifier) in basenames:
                matched += 1
            else:
                unresolved += 1
                if len(unresolved_samples) < MAX_UNRESOLVED_SAMPLES:
                    unresolved_samples.append(_display_identifier(identifier))
        entry["matched_internal_id_count"] = matched
        entry["unresolved_internal_id_count"] = unresolved
        entries.append(entry)
        internal_total += len(internal_ids)
        matched_total += matched
        unresolved_total += unresolved
        if entry["status"] == "parsed":
            validated_total += 1

    return {
        "catalogs": entries,
        "summary": {
            "catalog_candidate_count": len(entries),
            "validated_catalog_count": validated_total,
            "internal_id_count": internal_total,
            "matched_internal_id_count": matched_total,
            "unresolved_internal_id_count": unresolved_total,
            "unresolved_internal_id_samples": unresolved_samples,
            "key_mapping_status": "unsupported" if any(
                entry["key_mapping_status"] == "unsupported" for entry in entries
            ) else "absent",
        },
    }
