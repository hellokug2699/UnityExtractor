"""Conservative Addressables catalog inspection.

JSON catalogs are validated and their key/bucket/entry blobs are decoded
using the format written by Addressables 1.16+ JSON catalogs. Binary
catalogs are reported as unsupported. Locations that cannot be matched to
a discovered container remain visible instead of being silently dropped.
"""
from __future__ import annotations

import base64
import binascii
import json
import re
import struct
from pathlib import PurePosixPath
from typing import Any, Iterable

MAX_CATALOG_BYTES = 8 * 1024 * 1024
MAX_CATALOGS = 16
MAX_CATALOG_TOTAL_BYTES = 32 * 1024 * 1024
MAX_INTERNAL_IDS = 200_000
MAX_KEY_COUNT = 200_000
MAX_BUCKET_COUNT = 200_000
MAX_ENTRY_COUNT = 200_000
MAX_KEY_ENTRIES = 200_000
MAX_KEY_ENTRY_SAMPLES = 50
MAX_ID_LENGTH = 4096
MAX_PLACEHOLDERS = 16
MAX_UNRESOLVED_SAMPLES = 20
MAX_WARNING_VALUE_LENGTH = 200
MAX_CATALOG_WARNINGS = 50

CATALOG_SUFFIXES = {".json", ".bin", ".bytes"}
PLACEHOLDER_RE = re.compile(r"\{[^{}]+\}")
KEY_TYPES = {
    0: "ascii_string",
    1: "unicode_string",
    2: "uint16",
    3: "uint32",
    4: "int32",
    5: "hash128",
    6: "type",
    7: "json_object",
}


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


def _decode_base64(value: Any, name: str) -> bytes:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} is missing.")
    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"{name} is not valid base64: {exc}") from exc


def _read_int32(data: bytes, offset: int) -> int:
    if offset < 0 or offset + 4 > len(data):
        raise ValueError(f"Truncated int32 at offset {offset}.")
    return struct.unpack_from("<i", data, offset)[0]


def _read_bytes(data: bytes, offset: int, length: int) -> bytes:
    if length < 0 or offset < 0 or offset + length > len(data):
        raise ValueError(f"Truncated value at offset {offset}.")
    return data[offset : offset + length]


def _read_key(data: bytes, offset: int) -> tuple[str | None, str]:
    """Read one serialized catalog key without executing embedded objects."""
    if offset < 0 or offset >= len(data):
        raise ValueError(f"Key offset {offset} is outside key data.")
    kind = data[offset]
    type_name = KEY_TYPES.get(kind, f"unknown_{kind}")
    position = offset + 1
    if kind == 0:
        length = _read_int32(data, position)
        raw = _read_bytes(data, position + 4, length)
        return raw.decode("ascii", errors="replace")[:MAX_ID_LENGTH], type_name
    if kind == 1:
        length = _read_int32(data, position)
        raw = _read_bytes(data, position + 4, length)
        return raw.decode("utf-16-le", errors="replace")[:MAX_ID_LENGTH], type_name
    if kind == 2:
        return str(struct.unpack_from("<H", _read_bytes(data, position, 2))[0]), type_name
    if kind == 3:
        return str(struct.unpack_from("<I", _read_bytes(data, position, 4))[0]), type_name
    if kind == 4:
        return str(struct.unpack_from("<i", _read_bytes(data, position, 4))[0]), type_name
    if kind in (5, 6):
        length = _read_bytes(data, position, 1)[0]
        raw = _read_bytes(data, position + 1, length)
        return raw.decode("ascii", errors="replace")[:MAX_ID_LENGTH], type_name
    # JsonObject keys would require constructing a managed object. Never do that.
    return None, type_name


def decode_key_entries(
    payload: dict[str, Any],
    internal_ids: list[str],
    providers: list[Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Decode Addressables key/bucket/entry blobs into key-to-location rows.

    The layout follows the JSON catalog writer: little-endian ints, a bucket
    table pointing into key data, and fixed 28-byte entry records. Structural
    errors raise ValueError so the caller can keep the catalog visible as
    partially decoded.
    """
    key_data = _decode_base64(payload.get("m_KeyDataString"), "m_KeyDataString")
    bucket_data = _decode_base64(payload.get("m_BucketDataString"), "m_BucketDataString")
    entry_data = _decode_base64(payload.get("m_EntryDataString"), "m_EntryDataString")

    key_count = _read_int32(key_data, 0)
    if key_count < 0 or key_count > MAX_KEY_COUNT:
        raise ValueError(f"Unreasonable key count: {key_count}.")
    bucket_count = _read_int32(bucket_data, 0)
    if bucket_count < 0 or bucket_count > MAX_BUCKET_COUNT:
        raise ValueError(f"Unreasonable bucket count: {bucket_count}.")
    entry_count = _read_int32(entry_data, 0)
    if entry_count < 0 or entry_count > MAX_ENTRY_COUNT:
        raise ValueError(f"Unreasonable entry count: {entry_count}.")
    if 4 + entry_count * 28 > len(entry_data):
        raise ValueError("Entry data is shorter than its declared entry count.")

    buckets: list[tuple[int, list[int]]] = []
    position = 4
    for _ in range(bucket_count):
        key_offset = _read_int32(bucket_data, position)
        position += 4
        count = _read_int32(bucket_data, position)
        position += 4
        if count < 0 or count > MAX_ENTRY_COUNT:
            raise ValueError(f"Unreasonable bucket entry count: {count}.")
        raw = _read_bytes(bucket_data, position, count * 4)
        position += count * 4
        indices = list(struct.unpack_from(f"<{count}i", raw)) if count else []
        buckets.append((key_offset, indices))

    keys: list[str | None] = []
    unsupported_key_types = 0
    for key_offset, _ in buckets:
        key, _type_name = _read_key(key_data, key_offset)
        keys.append(key)
        if key is None:
            unsupported_key_types += 1

    warnings: list[str] = []
    if key_count != bucket_count:
        warnings.append(
            f"Key count ({key_count}) does not match bucket count ({bucket_count})."
        )
    if unsupported_key_types:
        warnings.append(
            f"{unsupported_key_types} key(s) use types that are not decoded."
        )

    locations: list[dict[str, Any]] = []
    for index in range(entry_count):
        base = 4 + index * 28
        (
            internal_index,
            provider_index,
            dependency_index,
            dependency_hash,
            data_index,
            primary_index,
            resource_type_index,
        ) = struct.unpack_from("<7i", entry_data, base)
        internal_id = (
            internal_ids[internal_index]
            if 0 <= internal_index < len(internal_ids)
            else None
        )
        provider_id = (
            providers[provider_index]
            if 0 <= provider_index < len(providers)
            and isinstance(providers[provider_index], str)
            else None
        )
        primary_key = keys[primary_index] if 0 <= primary_index < len(keys) else None
        dependency_key = (
            keys[dependency_index] if 0 <= dependency_index < len(keys) else None
        )
        locations.append({
            "internal_id": internal_id,
            "provider_id": provider_id,
            "primary_key": primary_key,
            "dependency_key": dependency_key,
            "dependency_hash": dependency_hash,
            "data_index": data_index,
            "resource_type_index": resource_type_index,
        })

    key_entries: list[dict[str, Any]] = []
    truncated = False
    for key, (_key_offset, indices) in zip(keys, buckets):
        if key is None:
            continue
        for entry_index in indices:
            if not 0 <= entry_index < len(locations):
                continue
            if len(key_entries) >= MAX_KEY_ENTRIES:
                truncated = True
                break
            location = locations[entry_index]
            key_entries.append({"key": key, **location})
        if truncated:
            break
    if truncated:
        warnings.append(f"Key-to-location rows truncated at {MAX_KEY_ENTRIES}.")

    stats = {
        "key_count": key_count,
        "bucket_count": bucket_count,
        "location_count": entry_count,
        "key_entry_count": len(key_entries),
        "unsupported_key_type_count": unsupported_key_types,
        "truncated": truncated,
        "warnings": warnings,
    }
    return key_entries, stats


def _sample_key_entry(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "key": _display_identifier(str(item["key"])),
        "internal_id": (
            _display_identifier(item["internal_id"])
            if isinstance(item["internal_id"], str)
            else None
        ),
        "provider_id": (
            _display_identifier(item["provider_id"])
            if isinstance(item["provider_id"], str)
            else None
        ),
        "primary_key": (
            _display_identifier(item["primary_key"])
            if isinstance(item["primary_key"], str)
            else None
        ),
        "dependency_key": (
            _display_identifier(item["dependency_key"])
            if isinstance(item["dependency_key"], str)
            else None
        ),
        "resource_type_index": item["resource_type_index"],
    }


def inspect_catalog(
    source_id: str, name: str, data: bytes
) -> tuple[dict[str, Any], list[str], list[dict[str, Any]]]:
    """Validate one catalog candidate and extract location-level metadata.

    The returned entry is JSON-safe. Internal IDs and key-to-location rows are
    returned separately so callers can resolve resources without bloating the
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
        "key_count": 0,
        "location_count": 0,
        "key_entry_count": 0,
        "key_entry_samples": [],
        "unsupported_key_type_count": 0,
        "warnings": [],
    }
    internal_ids: list[str] = []
    key_entries: list[dict[str, Any]] = []

    if not data:
        _add_warning(entry, "Catalog candidate is empty.")
        return entry, internal_ids, key_entries
    if len(data) > MAX_CATALOG_BYTES:
        _add_warning(
            entry, f"Catalog candidate exceeds the {MAX_CATALOG_BYTES}-byte limit."
        )
        return entry, internal_ids, key_entries

    stripped = data.lstrip()
    if not stripped.startswith(b"{"):
        entry["status"] = "unsupported_binary"
        _add_warning(
            entry, "Catalog candidate is not JSON; binary catalogs are not parsed."
        )
        return entry, internal_ids, key_entries

    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        _add_warning(entry, f"Catalog is not valid UTF-8: {exc}")
        return entry, internal_ids, key_entries

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        _add_warning(entry, f"Catalog JSON is invalid: {exc}")
        return entry, internal_ids, key_entries
    if not isinstance(payload, dict):
        _add_warning(entry, "Catalog JSON root is not an object.")
        return entry, internal_ids, key_entries

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
        return entry, internal_ids, key_entries

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

    providers = raw_providers if isinstance(raw_providers, list) else []
    if not isinstance(raw_providers, list):
        _add_warning(entry, "m_ProviderIds is missing or is not a list.")

    entry["internal_id_count"] = len(internal_ids)
    entry["provider_id_count"] = len(providers)

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
    bucket_data = payload.get("m_BucketDataString")
    entry_data = payload.get("m_EntryDataString")
    if isinstance(key_data, str) and key_data:
        if (
            isinstance(bucket_data, str)
            and bucket_data
            and isinstance(entry_data, str)
            and entry_data
        ):
            try:
                key_entries, stats = decode_key_entries(
                    payload, internal_ids, providers
                )
            except (ValueError, struct.error) as exc:
                entry["key_mapping_status"] = "failed"
                _add_warning(
                    entry, f"Catalog key mapping could not be decoded: {exc}"
                )
            else:
                entry["key_mapping_status"] = "decoded"
                entry["key_count"] = stats["key_count"]
                entry["location_count"] = stats["location_count"]
                entry["key_entry_count"] = stats["key_entry_count"]
                entry["unsupported_key_type_count"] = stats[
                    "unsupported_key_type_count"
                ]
                entry["key_entry_samples"] = [
                    _sample_key_entry(item)
                    for item in key_entries[:MAX_KEY_ENTRY_SAMPLES]
                ]
                for warning in stats["warnings"]:
                    _add_warning(entry, warning)
        else:
            entry["key_mapping_status"] = "incomplete"
            _add_warning(
                entry,
                "Catalog key/bucket/entry data is incomplete; "
                "key mapping was not decoded.",
            )
    elif key_data is not None:
        _add_warning(entry, "m_KeyDataString has an unsupported type.")

    return entry, internal_ids, key_entries


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


def _overall_key_mapping_status(entries: Iterable[dict[str, Any]]) -> str:
    statuses = {entry["key_mapping_status"] for entry in entries}
    for status in ("decoded", "failed", "incomplete"):
        if status in statuses:
            return status
    return "absent"


def build_addressables_report(
    catalogs: Iterable[tuple[dict[str, Any], list[str], list[dict[str, Any]]]],
    source_ids: Iterable[str],
) -> dict[str, Any]:
    """Attach container-match counts and a bounded global summary.

    Matching is filename-based on purpose: only relationships the scan can
    actually observe are reported. Unmatched internal IDs and key rows remain
    counted and sampled instead of being silently discarded.
    """
    basenames = {_source_basename(value) for value in source_ids if value}
    basenames.discard("")

    entries: list[dict[str, Any]] = []
    internal_total = 0
    matched_total = 0
    unresolved_total = 0
    unresolved_samples: list[str] = []
    validated_total = 0
    key_entry_total = 0
    matched_key_entry_total = 0
    unresolved_key_entry_total = 0
    unresolved_key_entry_samples: list[dict[str, str]] = []

    for entry, internal_ids, key_entries in catalogs:
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

        matched_keys = 0
        unresolved_keys = 0
        for item in key_entries:
            internal_id = item["internal_id"]
            if (
                isinstance(internal_id, str)
                and _identifier_basename(internal_id) in basenames
            ):
                matched_keys += 1
            else:
                unresolved_keys += 1
                if len(unresolved_key_entry_samples) < MAX_UNRESOLVED_SAMPLES:
                    unresolved_key_entry_samples.append({
                        "key": _display_identifier(str(item["key"])),
                        "internal_id": (
                            _display_identifier(internal_id)
                            if isinstance(internal_id, str)
                            else ""
                        ),
                    })
        entry["matched_key_entry_count"] = matched_keys
        entry["unresolved_key_entry_count"] = unresolved_keys

        entries.append(entry)
        internal_total += len(internal_ids)
        matched_total += matched
        unresolved_total += unresolved
        key_entry_total += len(key_entries)
        matched_key_entry_total += matched_keys
        unresolved_key_entry_total += unresolved_keys
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
            "key_mapping_status": _overall_key_mapping_status(entries),
            "key_entry_count": key_entry_total,
            "matched_key_entry_count": matched_key_entry_total,
            "unresolved_key_entry_count": unresolved_key_entry_total,
            "unresolved_key_entry_samples": unresolved_key_entry_samples,
        },
    }
