from __future__ import annotations

import json

from unity_extractor.addressables import (
    build_addressables_report,
    inspect_catalog,
    is_catalog_candidate_name,
)


def make_catalog(**overrides) -> bytes:
    payload = {
        "m_LocatorId": "AddressablesMainContentCatalog",
        "m_BuildResultHash": "build-hash",
        "m_InternalIds": [
            "{UnityEngine.AddressableAssets.Addressables.RuntimePath}/Android/level.bundle",
            "Assets/Scenes/Main.unity",
        ],
        "m_ProviderIds": [
            "UnityEngine.ResourceManagement.ResourceProviders.AssetBundleProvider",
            "UnityEngine.ResourceManagement.ResourceProviders.BundledAssetProvider",
        ],
        "m_KeyDataString": "AQAAAA==",
    }
    payload.update(overrides)
    return json.dumps(payload).encode("utf-8")


def test_catalog_candidate_filenames_are_detected_without_fixed_paths():
    assert is_catalog_candidate_name("assets/aa/catalog.json")
    assert is_catalog_candidate_name("assets/aa/catalog_2026.bin")
    assert is_catalog_candidate_name("Catalog.bytes")
    assert not is_catalog_candidate_name("assets/settings.json")
    assert not is_catalog_candidate_name("assets/catalog.hash")


def test_json_catalog_is_validated_and_internal_ids_are_extracted():
    entry, internal_ids = inspect_catalog(
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
    assert entry["key_mapping_status"] == "unsupported"
    assert len(internal_ids) == 2


def test_json_without_catalog_markers_is_not_assumed_to_be_addressables():
    entry, internal_ids = inspect_catalog(
        "game.apk!/assets/catalog.json", "catalog.json", b'{"hello": "world"}'
    )

    assert entry["status"] == "candidate_unvalidated"
    assert internal_ids == []
    assert "markers" in entry["warnings"][0]


def test_invalid_catalog_json_is_reported_as_failed():
    entry, internal_ids = inspect_catalog(
        "game.apk!/assets/catalog.json", "catalog.json", b"{not json"
    )

    assert entry["status"] == "failed"
    assert internal_ids == []
    assert "invalid" in entry["warnings"][0]


def test_binary_catalog_is_unsupported_rather_than_guessed():
    entry, internal_ids = inspect_catalog(
        "game.apk!/assets/catalog.bin", "catalog.bin", b"\x00\x01binary"
    )

    assert entry["status"] == "unsupported_binary"
    assert internal_ids == []
    assert "not JSON" in entry["warnings"][0]


def test_non_string_internal_ids_are_skipped_with_a_warning():
    entry, internal_ids = inspect_catalog(
        "game.apk!/assets/catalog.json",
        "catalog.json",
        make_catalog(m_InternalIds=["valid.bundle", 42, None]),
    )

    assert entry["status"] == "parsed"
    assert internal_ids == ["valid.bundle"]
    assert entry["internal_id_count"] == 1
    assert any("non-string internal ID" in warning for warning in entry["warnings"])


def test_report_matches_internal_ids_by_container_filename_and_keeps_unresolved():
    entry, internal_ids = inspect_catalog(
        "game.apk!/assets/aa/catalog.json", "catalog.json", make_catalog()
    )

    report = build_addressables_report(
        [(entry, internal_ids)],
        ["game.apk!/assets/aa/Android/level.bundle"],
    )

    summary = report["summary"]
    assert summary["catalog_candidate_count"] == 1
    assert summary["validated_catalog_count"] == 1
    assert summary["internal_id_count"] == 2
    assert summary["matched_internal_id_count"] == 1
    assert summary["unresolved_internal_id_count"] == 1
    assert summary["unresolved_internal_id_samples"] == ["Assets/Scenes/Main.unity"]
    assert summary["key_mapping_status"] == "unsupported"
    assert report["catalogs"][0]["matched_internal_id_count"] == 1
    assert report["catalogs"][0]["unresolved_internal_id_count"] == 1


def test_empty_catalog_candidate_is_reported_as_failed():
    entry, internal_ids = inspect_catalog(
        "game.apk!/assets/catalog.json", "catalog.json", b""
    )

    assert entry["status"] == "failed"
    assert internal_ids == []
    assert "empty" in entry["warnings"][0]
