from __future__ import annotations

import base64
import json
import struct

from unity_extractor.addressables import (
    build_addressables_report,
    inspect_catalog,
    is_catalog_candidate_name,
)


def encode_string_key(value: str, *, unicode: bool = False) -> bytes:
    raw = value.encode("utf-16-le" if unicode else "ascii")
    return bytes([1 if unicode else 0]) + struct.pack("<i", len(raw)) + raw


def make_catalog_payload(
    keys: list[bytes],
    entries: list[tuple[int, int, int, int, int, int, int]],
    *,
    internal_ids: list[str] | None = None,
    providers: list[str] | None = None,
) -> dict:
    key_data = struct.pack("<i", len(keys))
    key_offsets = []
    for key in keys:
        key_offsets.append(len(key_data))
        key_data += key

    bucket_data = struct.pack("<i", len(keys))
    for index, key_offset in enumerate(key_offsets):
        bucket_data += struct.pack("<iii", key_offset, 1, index)

    entry_data = struct.pack("<i", len(entries))
    for entry in entries:
        entry_data += struct.pack("<7i", *entry)

    return {
        "m_LocatorId": "AddressablesMainContentCatalog",
        "m_BuildResultHash": "build-hash",
        "m_InternalIds": internal_ids
        if internal_ids is not None
        else [
            "{UnityEngine.AddressableAssets.Addressables.RuntimePath}/Android/level.bundle",
            "Assets/Scenes/Main.unity",
        ],
        "m_ProviderIds": providers
        if providers is not None
        else [
            "UnityEngine.ResourceManagement.ResourceProviders.AssetBundleProvider",
            "UnityEngine.ResourceManagement.ResourceProviders.BundledAssetProvider",
        ],
        "m_KeyDataString": base64.b64encode(key_data).decode("ascii"),
        "m_BucketDataString": base64.b64encode(bucket_data).decode("ascii"),
        "m_EntryDataString": base64.b64encode(entry_data).decode("ascii"),
    }


def make_catalog(**overrides) -> bytes:
    payload = make_catalog_payload(
        [encode_string_key("song.0")],
        [(0, 0, -1, 0, -1, 0, 0)],
    )
    payload.update(overrides)
    return json.dumps(payload).encode("utf-8")


def test_catalog_candidate_filenames_are_detected_without_fixed_paths():
    assert is_catalog_candidate_name("assets/aa/catalog.json")
    assert is_catalog_candidate_name("assets/aa/catalog_2026.bin")
    assert is_catalog_candidate_name("Catalog.bytes")
    assert not is_catalog_candidate_name("assets/settings.json")
    assert not is_catalog_candidate_name("assets/catalog.hash")


def test_json_catalog_is_validated_and_key_rows_are_decoded():
    entry, internal_ids, key_entries = inspect_catalog(
        "game.apk!/assets/aa/catalog.json", "catalog.json", make_catalog()
    )

    assert entry["status"] == "parsed"
    assert entry["format"] == "json"
    assert entry["locator_id"] == "AddressablesMainContentCatalog"
    assert entry["build_result_hash"] == "build-hash"
    assert entry["internal_id_count"] == 2
    assert entry["provider_id_count"] == 2
    assert entry["runtime_path_placeholders"] == [
        "{UnityEngine.AddressableAssets.Addressables.RuntimePath}"
    ]
    assert entry["key_mapping_status"] == "decoded"
    assert entry["key_count"] == 1
    assert entry["location_count"] == 1
    assert entry["key_entry_count"] == 1
    assert len(internal_ids) == 2
    assert key_entries == [{
        "key": "song.0",
        "internal_id": (
            "{UnityEngine.AddressableAssets.Addressables.RuntimePath}"
            "/Android/level.bundle"
        ),
        "provider_id": (
            "UnityEngine.ResourceManagement.ResourceProviders.AssetBundleProvider"
        ),
        "primary_key": "song.0",
        "dependency_key": None,
        "dependency_hash": 0,
        "data_index": -1,
        "resource_type_index": 0,
    }]
    assert entry["key_entry_samples"][0]["key"] == "song.0"


def test_dependency_and_secondary_keys_are_preserved():
    keys = [encode_string_key("song.0"), encode_string_key("dep.bundle")]
    key_data = struct.pack("<i", len(keys))
    offsets = []
    for key in keys:
        offsets.append(len(key_data))
        key_data += key
    bucket_data = struct.pack("<i", len(keys))
    for key_offset in offsets:
        bucket_data += struct.pack("<iii", key_offset, 1, 0)
    entry_data = struct.pack("<i", 1) + struct.pack("<7i", 0, 0, 1, 0, -1, 0, 0)
    payload = {
        "m_LocatorId": "Main",
        "m_InternalIds": ["level.bundle"],
        "m_ProviderIds": ["AssetBundleProvider"],
        "m_KeyDataString": base64.b64encode(key_data).decode("ascii"),
        "m_BucketDataString": base64.b64encode(bucket_data).decode("ascii"),
        "m_EntryDataString": base64.b64encode(entry_data).decode("ascii"),
    }
    entry, _internal_ids, key_entries = inspect_catalog(
        "game.apk!/catalog.json",
        "catalog.json",
        json.dumps(payload).encode("utf-8"),
    )

    assert entry["key_mapping_status"] == "decoded"
    assert entry["key_entry_count"] == 2
    assert {row["key"] for row in key_entries} == {"song.0", "dep.bundle"}
    assert all(row["dependency_key"] == "dep.bundle" for row in key_entries)
    assert all(row["primary_key"] == "song.0" for row in key_entries)


def test_unicode_and_numeric_keys_are_decoded_without_object_construction():
    key_data = struct.pack("<i", 2)
    first_offset = len(key_data)
    key_data += encode_string_key("歌曲", unicode=True)
    second_offset = len(key_data)
    key_data += b"\x02" + struct.pack("<H", 42)
    bucket_data = struct.pack("<i", 2)
    bucket_data += struct.pack("<iii", first_offset, 1, 0)
    bucket_data += struct.pack("<iii", second_offset, 1, 0)
    entry_data = struct.pack("<i", 1) + struct.pack("<7i", 0, 0, -1, 0, -1, 0, 0)
    payload = {
        "m_LocatorId": "Main",
        "m_InternalIds": ["level.bundle"],
        "m_ProviderIds": ["AssetBundleProvider"],
        "m_KeyDataString": base64.b64encode(key_data).decode("ascii"),
        "m_BucketDataString": base64.b64encode(bucket_data).decode("ascii"),
        "m_EntryDataString": base64.b64encode(entry_data).decode("ascii"),
    }

    entry, _internal_ids, key_entries = inspect_catalog(
        "game.apk!/catalog.json", "catalog.json", json.dumps(payload).encode("utf-8")
    )

    assert entry["key_mapping_status"] == "decoded"
    assert {row["key"] for row in key_entries} == {"歌曲", "42"}


def test_json_object_keys_are_counted_but_not_evaluated():
    key_data = struct.pack("<i", 1) + b"\x07"
    bucket_data = struct.pack("<i", 1) + struct.pack("<iii", 4, 1, 0)
    entry_data = struct.pack("<i", 1) + struct.pack("<7i", 0, 0, -1, 0, -1, 0, 0)
    payload = {
        "m_LocatorId": "Main",
        "m_InternalIds": ["level.bundle"],
        "m_ProviderIds": ["AssetBundleProvider"],
        "m_KeyDataString": base64.b64encode(key_data).decode("ascii"),
        "m_BucketDataString": base64.b64encode(bucket_data).decode("ascii"),
        "m_EntryDataString": base64.b64encode(entry_data).decode("ascii"),
    }

    entry, _internal_ids, key_entries = inspect_catalog(
        "game.apk!/catalog.json", "catalog.json", json.dumps(payload).encode("utf-8")
    )

    assert entry["key_mapping_status"] == "decoded"
    assert entry["unsupported_key_type_count"] == 1
    assert key_entries == []


def test_missing_key_blobs_are_reported_as_incomplete():
    payload = {
        "m_LocatorId": "Main",
        "m_InternalIds": ["level.bundle"],
        "m_ProviderIds": ["AssetBundleProvider"],
        "m_KeyDataString": "AQAAAA==",
    }

    entry, _internal_ids, key_entries = inspect_catalog(
        "game.apk!/catalog.json", "catalog.json", json.dumps(payload).encode("utf-8")
    )

    assert entry["status"] == "parsed"
    assert entry["key_mapping_status"] == "incomplete"
    assert key_entries == []
    assert any("incomplete" in warning for warning in entry["warnings"])


def test_truncated_entry_blob_keeps_catalog_visible_as_failed_mapping():
    payload = make_catalog_payload(
        [encode_string_key("song.0")],
        [(0, 0, -1, 0, -1, 0, 0)],
    )
    payload["m_EntryDataString"] = base64.b64encode(struct.pack("<i", 1)).decode("ascii")

    entry, _internal_ids, key_entries = inspect_catalog(
        "game.apk!/catalog.json", "catalog.json", json.dumps(payload).encode("utf-8")
    )

    assert entry["status"] == "parsed"
    assert entry["key_mapping_status"] == "failed"
    assert key_entries == []
    assert any("could not be decoded" in warning for warning in entry["warnings"])


def test_json_without_catalog_markers_is_not_assumed_to_be_addressables():
    entry, internal_ids, key_entries = inspect_catalog(
        "game.apk!/assets/catalog.json", "catalog.json", b'{"hello": "world"}'
    )

    assert entry["status"] == "candidate_unvalidated"
    assert internal_ids == []
    assert key_entries == []
    assert "markers" in entry["warnings"][0]


def test_invalid_catalog_json_is_reported_as_failed():
    entry, internal_ids, key_entries = inspect_catalog(
        "game.apk!/assets/catalog.json", "catalog.json", b"{not json"
    )

    assert entry["status"] == "failed"
    assert internal_ids == []
    assert key_entries == []
    assert "invalid" in entry["warnings"][0]


def test_binary_catalog_is_unsupported_rather_than_guessed():
    entry, internal_ids, key_entries = inspect_catalog(
        "game.apk!/assets/catalog.bin", "catalog.bin", b"\x00\x01binary"
    )

    assert entry["status"] == "unsupported_binary"
    assert internal_ids == []
    assert key_entries == []
    assert "not JSON" in entry["warnings"][0]


def test_non_string_internal_ids_are_skipped_with_a_warning():
    payload = make_catalog_payload(
        [encode_string_key("song.0")],
        [(0, 0, -1, 0, -1, 0, 0)],
        internal_ids=["valid.bundle", 42, None],  # type: ignore[list-item]
    )

    entry, internal_ids, _key_entries = inspect_catalog(
        "game.apk!/assets/catalog.json",
        "catalog.json",
        json.dumps(payload).encode("utf-8"),
    )

    assert entry["status"] == "parsed"
    assert internal_ids == ["valid.bundle"]
    assert entry["internal_id_count"] == 1
    assert any("non-string internal ID" in warning for warning in entry["warnings"])


def test_report_matches_internal_ids_and_key_rows_and_keeps_unresolved():
    entry, internal_ids, key_entries = inspect_catalog(
        "game.apk!/assets/aa/catalog.json", "catalog.json", make_catalog()
    )

    report = build_addressables_report(
        [(entry, internal_ids, key_entries)],
        ["game.apk!/assets/aa/Android/level.bundle"],
    )

    summary = report["summary"]
    assert summary["catalog_candidate_count"] == 1
    assert summary["validated_catalog_count"] == 1
    assert summary["internal_id_count"] == 2
    assert summary["matched_internal_id_count"] == 1
    assert summary["unresolved_internal_id_count"] == 1
    assert summary["unresolved_internal_id_samples"] == ["Assets/Scenes/Main.unity"]
    assert summary["key_mapping_status"] == "decoded"
    assert summary["key_entry_count"] == 1
    assert summary["matched_key_entry_count"] == 1
    assert summary["unresolved_key_entry_count"] == 0
    assert report["catalogs"][0]["matched_key_entry_count"] == 1


def test_unresolved_key_rows_are_sampled():
    entry, internal_ids, key_entries = inspect_catalog(
        "game.apk!/assets/aa/catalog.json", "catalog.json", make_catalog()
    )

    report = build_addressables_report([(entry, internal_ids, key_entries)], [])

    summary = report["summary"]
    assert summary["matched_key_entry_count"] == 0
    assert summary["unresolved_key_entry_count"] == 1
    assert summary["unresolved_key_entry_samples"][0]["key"] == "song.0"


def test_empty_catalog_candidate_is_reported_as_failed():
    entry, internal_ids, key_entries = inspect_catalog(
        "game.apk!/assets/catalog.json", "catalog.json", b""
    )

    assert entry["status"] == "failed"
    assert internal_ids == []
    assert key_entries == []
    assert "empty" in entry["warnings"][0]
