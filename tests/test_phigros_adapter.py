from __future__ import annotations

import json
from types import SimpleNamespace

from unity_extractor.adapters.phigros import (
    PhigrosAdapter,
    normalize_difficulty,
    parse_chart_payload,
)
from unity_extractor.object_models import EnumerationReport, ObjectRecord
from unity_extractor.preset import load_plan_destinations


def chart_payload(*, bpm: float = 180.0, format_version: int = 3, offset: float = 0.1) -> str:
    return json.dumps({
        "formatVersion": format_version,
        "offset": offset,
        "numOfNotes": 3,
        "judgeLineList": [{
            "bpm": bpm,
            "notesAbove": [
                {"type": 1, "time": 1.0, "positionX": 0.0},
                {"type": 3, "time": 2.0, "holdTime": 0.5},
            ],
            "notesBelow": [
                {"type": 2, "time": 1.5, "holdTime": 0.0},
            ],
        }],
    })


class FakeChartObject:
    def __init__(self, payload: str | bytes):
        self.payload = payload

    def parse_as_object(self):
        return SimpleNamespace(m_Script=self.payload)


class BrokenChartObject:
    def parse_as_object(self):
        raise ValueError("synthetic chart decode failure")


def make_record(
    resource_id: str,
    type_name: str,
    name: str,
    bundle: str,
    *,
    path_id: int = 1,
) -> ObjectRecord:
    return ObjectRecord(
        resource_id=resource_id,
        source_id=f"game.apk!/assets/aa/Android/{bundle}",
        container_id=f"CAB-{bundle}",
        path_id=path_id,
        type_name=type_name,
        name=name,
    )


def make_report(
    objects: list[ObjectRecord],
    key_entries: list[dict] | None = None,
) -> EnumerationReport:
    return EnumerationReport(
        schema_version="1.0",
        input_path="game.apk",
        objects=objects,
        catalog_key_entries=key_entries or [],
    )


def track_entry(key: str, internal_id: str) -> dict:
    return {
        "key": key,
        "internal_id": internal_id,
        "provider_id": "AssetBundleProvider",
        "primary_key": None,
        "dependency_key": None,
        "resource_type_index": 0,
    }


def observe_chart(adapter: PhigrosAdapter, record: ObjectRecord, payload: str) -> None:
    adapter(FakeChartObject(payload), record)


def test_chart_payload_metadata_is_normalized():
    metadata = parse_chart_payload(chart_payload(), normalize_difficulty("IN"))

    assert metadata["status"] == "parsed"
    assert metadata["format_version"] == 3
    assert metadata["offset_seconds"] == 0.1
    assert metadata["declared_note_count"] == 3
    assert metadata["computed_note_count"] == 3
    assert metadata["judge_line_count"] == 1
    assert metadata["bpm_min"] == 180.0
    assert metadata["bpm_max"] == 180.0
    assert metadata["bpm_values"] == [180.0]
    assert metadata["note_type_counts"] == {"1": 1, "3": 1, "2": 1}
    assert metadata["duration_seconds"] == 2.5
    assert metadata["difficulty"]["code"] == "IN"
    assert metadata["difficulty"]["name"] == "Insane"
    assert metadata["warnings"] == []


def test_difficulty_normalization_handles_error_variants_and_unknown_codes():
    assert normalize_difficulty("ez")["name"] == "Easy"
    assert normalize_difficulty("AT")["recognized"] is True
    error = normalize_difficulty("HD_Error")
    assert error["code"] == "HD"
    assert error["error_variant"] is True
    assert error["recognized"] is True
    unknown = normalize_difficulty("XX")
    assert unknown["recognized"] is False
    assert unknown["name"] is None


def test_malformed_chart_payload_returns_diagnostics():
    metadata = parse_chart_payload("{not json", normalize_difficulty("IN"))

    assert metadata["status"] == "failed"
    assert metadata["warnings"]


def test_adapter_parses_chart_objects_while_container_is_loaded():
    adapter = PhigrosAdapter()
    record = make_record("chart-1", "TextAsset", "Chart_IN", "chart.bundle")

    observe_chart(adapter, record, chart_payload())
    result = adapter.analyze(make_report([record]))

    assert result["detection"]["status"] == "detected"
    assert result["detection"]["confidence"] == "low"
    assert result["summary"]["parsed_chart_count"] == 1
    assert result["summary"]["unassociated_chart_count"] == 1
    chart = result["unassociated_charts"][0]
    assert chart["difficulty"]["code"] == "IN"
    assert chart["chart_metadata"]["bpm_min"] == 180.0
    assert result["songs"] == []
    assert any("song IDs cannot be associated" in item for item in result["diagnostics"])
    assert result["export_preset"]["entries"][0]["destination"].startswith(
        "phigros/unassociated/charts/Chart_IN__"
    )


def test_catalog_associates_charts_music_and_illustrations(tmp_path):
    chart_in = make_record("chart-in", "TextAsset", "Chart_IN", "chart-in.bundle")
    chart_hd = make_record("chart-hd", "TextAsset", "Chart_HD", "chart-hd.bundle")
    music = make_record("music-1", "AudioClip", "music", "music.bundle")
    illustration = make_record("image-1", "Texture2D", "Illustration", "image.bundle")
    adapter = PhigrosAdapter()
    observe_chart(adapter, chart_in, chart_payload(bpm=200.0))
    observe_chart(adapter, chart_hd, chart_payload(bpm=150.0))

    report = make_report(
        [chart_in, chart_hd, music, illustration],
        [
            track_entry("Assets/Tracks/1001/Chart_IN.json", "{RuntimePath}/Android/chart-in.bundle"),
            track_entry("Assets/Tracks/1001/music.wav", "{RuntimePath}/Android/music.bundle"),
            track_entry("Assets/Tracks/1001/Illustration.jpg", "{RuntimePath}/Android/image.bundle"),
            track_entry("Assets/Tracks/1002/Chart_HD.json", "{RuntimePath}/Android/chart-hd.bundle"),
        ],
    )

    result = adapter.analyze(report)

    assert result["detection"]["status"] == "detected"
    assert result["detection"]["confidence"] == "high"
    assert result["summary"]["song_count"] == 2
    assert result["summary"]["difficulty_count"] == 2
    assert result["summary"]["associated_chart_count"] == 2
    assert result["summary"]["matched_track_key_count"] == 4

    songs = {song["song_id"]: song for song in result["songs"]}
    assert set(songs) == {"1001", "1002"}
    song = songs["1001"]
    assert song["title"] is None
    assert song["artist"] is None
    assert song["metadata_status"] == "unavailable"
    assert song["difficulties"]["IN"]["chart_metadata"]["bpm_max"] == 200.0
    assert song["audio"][0]["resource_id"] == "music-1"
    assert song["illustrations"][0]["resource_id"] == "image-1"
    assert songs["1002"]["difficulties"]["HD"]["chart_metadata"]["bpm_min"] == 150.0

    destinations = {
        entry["destination"] for entry in result["export_preset"]["entries"]
    }
    assert "phigros/songs/1001/charts/in.json" in destinations
    assert "phigros/songs/1001/charts/hd.json" not in destinations
    assert "phigros/songs/1001/audio/music.wav" in destinations
    assert "phigros/songs/1001/illustrations/Illustration.png" in destinations
    assert "phigros/songs/1002/charts/hd.json" in destinations
    assert any("Song titles and artists" in item for item in result["diagnostics"])

    plan_path = tmp_path / "phigros.json"
    plan_path.write_text(json.dumps(result), encoding="utf-8")
    loaded = load_plan_destinations(plan_path)
    assert loaded["chart-in"] == "phigros/songs/1001/charts/in.json"
    assert loaded["music-1"] == "phigros/songs/1001/audio/music.wav"
    assert loaded["image-1"] == "phigros/songs/1001/illustrations/Illustration.png"


def test_unmatched_catalog_asset_stays_visible_as_diagnostic():
    adapter = PhigrosAdapter()
    report = make_report(
        [],
        [track_entry("Assets/Tracks/2001/Chart_IN.json", "{RuntimePath}/Android/missing.bundle")],
    )

    result = adapter.analyze(report)

    assert result["summary"]["song_count"] == 1
    assert result["summary"]["unmatched_track_key_count"] == 1
    song = result["songs"][0]
    assert song["difficulties"] == {}
    assert song["unmatched_assets"][0]["asset"] == "Chart_IN.json"
    assert song["diagnostics"]
    assert result["export_preset"]["entries"] == []


def test_broken_chart_object_does_not_abort_adapter():
    adapter = PhigrosAdapter()
    record = make_record("chart-bad", "TextAsset", "Chart_EZ", "bad.bundle")

    adapter(BrokenChartObject(), record)
    result = adapter.analyze(make_report([record]))

    assert result["summary"]["failed_chart_count"] == 1
    assert result["summary"]["parsed_chart_count"] == 0
    chart = result["unassociated_charts"][0]
    assert chart["chart_metadata"]["status"] == "failed"


def test_full_catalog_key_rows_are_not_serialized_in_scan_report():
    report = make_report(
        [],
        [track_entry("Assets/Tracks/1/Chart_IN.json", "level.bundle")],
    )

    assert "catalog_key_entries" not in report.to_dict()
