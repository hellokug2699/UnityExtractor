"""Phigros semantic adapter.

All Phigros-specific names, paths and chart rules live here. Core scanning
and export modules do not import this adapter.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ..object_models import EnumerationReport, ObjectRecord

DIFFICULTY_NAMES = {
    "EZ": "Easy",
    "HD": "Hard",
    "IN": "Insane",
    "AT": "Another",
    "SP": "Special",
    "LEGACY": "Legacy",
}
DIFFICULTY_ORDER = {
    "EZ": 1,
    "HD": 2,
    "IN": 3,
    "AT": 4,
    "SP": 5,
    "LEGACY": 6,
}
CHART_NAME_RE = re.compile(r"^Chart_(?P<difficulty>[A-Za-z0-9_]+)$", re.IGNORECASE)
CHART_ASSET_RE = re.compile(
    r"^Chart_(?P<difficulty>[A-Za-z0-9_]+)\.(?:json|txt|bytes)$", re.IGNORECASE
)
TRACK_KEY_RE = re.compile(
    r"^Assets/Tracks/(?P<song_id>[^/\\]+)/(?P<asset>[^/\\]+)$", re.IGNORECASE
)
SAFE_SEGMENT_RE = re.compile(r"[^A-Za-z0-9._-]+")
MAX_DIAGNOSTICS = 20
MAX_INLINE_BPM_VALUES = 20
MAX_UNMATCHED_ASSETS = 20


def _safe_segment(value: str, fallback: str = "unnamed") -> str:
    candidate = SAFE_SEGMENT_RE.sub("_", value or "").strip("._-")
    return candidate[:80] or fallback


def _append_diagnostic(target: list[str], message: str) -> None:
    if len(target) < MAX_DIAGNOSTICS:
        target.append(message)


def normalize_difficulty(value: str) -> dict[str, Any]:
    """Normalize a Phigros chart difficulty label without guessing ranks."""
    code = (value or "").strip().upper()
    error_variant = code.endswith("_ERROR")
    if error_variant:
        code = code[: -len("_ERROR")]
    return {
        "code": code or None,
        "name": DIFFICULTY_NAMES.get(code),
        "recognized": code in DIFFICULTY_NAMES,
        "error_variant": error_variant,
        "sort_order": DIFFICULTY_ORDER.get(code, 99),
    }


def parse_chart_name(name: str | None) -> dict[str, Any] | None:
    if not name:
        return None
    match = CHART_NAME_RE.match(name)
    if not match:
        return None
    return normalize_difficulty(match.group("difficulty"))


def _number(value: Any) -> int | float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    return None


def parse_chart_payload(payload: Any, difficulty: dict[str, Any] | None = None) -> dict[str, Any]:
    """Parse a Phigros chart payload into conservative, normalized metadata."""
    result: dict[str, Any] = {
        "status": "failed",
        "difficulty": difficulty,
        "format_version": None,
        "offset_seconds": None,
        "declared_note_count": None,
        "computed_note_count": None,
        "judge_line_count": None,
        "bpm_min": None,
        "bpm_max": None,
        "bpm_values": [],
        "note_type_counts": {},
        "duration_seconds": None,
        "warnings": [],
    }
    try:
        if isinstance(payload, (bytes, bytearray, memoryview)):
            text = bytes(payload).decode("utf-8-sig")
        elif isinstance(payload, str):
            text = payload
        else:
            raise TypeError(f"Unsupported chart payload type: {type(payload).__name__}")
        data = json.loads(text)
    except Exception as exc:
        result["warnings"].append(f"{type(exc).__name__}: {exc}")
        return result

    if not isinstance(data, dict):
        result["warnings"].append("Chart JSON root is not an object.")
        return result

    result["status"] = "parsed"
    format_version = data.get("formatVersion")
    if isinstance(format_version, (int, float, str)) and not isinstance(format_version, bool):
        result["format_version"] = format_version
    else:
        result["warnings"].append("formatVersion is missing or has an unsupported type.")

    offset = _number(data.get("offset"))
    if offset is not None:
        result["offset_seconds"] = offset
    else:
        result["warnings"].append("offset is missing or is not numeric.")

    declared = data.get("numOfNotes")
    if isinstance(declared, int) and not isinstance(declared, bool):
        result["declared_note_count"] = declared

    judge_lines = data.get("judgeLineList")
    if not isinstance(judge_lines, list):
        result["warnings"].append("judgeLineList is missing or is not a list.")
        return result
    result["judge_line_count"] = len(judge_lines)

    bpm_values: set[float] = set()
    note_type_counts: dict[str, int] = {}
    computed_notes = 0
    duration = 0.0
    has_note_time = False

    for line in judge_lines:
        if not isinstance(line, dict):
            continue
        bpm = _number(line.get("bpm"))
        if bpm is not None and bpm > 0:
            bpm_values.add(round(float(bpm), 4))
        for key in ("notesAbove", "notesBelow"):
            notes = line.get(key)
            if not isinstance(notes, list):
                continue
            for note in notes:
                if not isinstance(note, dict):
                    continue
                computed_notes += 1
                note_type = note.get("type")
                type_key = str(note_type) if note_type is not None else "unknown"
                note_type_counts[type_key] = note_type_counts.get(type_key, 0) + 1
                start = _number(note.get("time"))
                if start is not None:
                    has_note_time = True
                    hold = _number(note.get("holdTime")) or 0.0
                    duration = max(duration, float(start) + max(float(hold), 0.0))

    result["computed_note_count"] = computed_notes
    result["note_type_counts"] = note_type_counts
    if has_note_time:
        result["duration_seconds"] = round(duration, 4)
    if bpm_values:
        ordered = sorted(bpm_values)
        result["bpm_min"] = ordered[0]
        result["bpm_max"] = ordered[-1]
        result["bpm_values"] = ordered[:MAX_INLINE_BPM_VALUES]
    else:
        result["warnings"].append("No numeric judge-line BPM values were found.")
    if computed_notes == 0:
        result["warnings"].append("No notes were found in judgeLineList.")
    return result


def _resource_ref(record: ObjectRecord) -> dict[str, Any]:
    return {
        "resource_id": record.resource_id,
        "type_name": record.type_name,
        "name": record.name,
        "source_id": record.source_id,
        "container_id": record.container_id,
        "path_id": record.path_id,
    }


def _basename(value: str | None) -> str:
    if not value:
        return ""
    return value.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1].lower()


def _asset_suffix(asset: str) -> str:
    suffix = Path(asset).suffix.lower()
    return suffix if len(suffix) <= 8 else ""


def _sort_song_id(value: str) -> tuple[int, Any]:
    try:
        return (0, int(value))
    except (TypeError, ValueError):
        return (1, value)


class PhigrosAdapter:
    name = "phigros"

    def __init__(self) -> None:
        self._charts: dict[str, dict[str, Any]] = {}

    def __call__(self, obj: Any, record: ObjectRecord) -> None:
        if record.type_name != "TextAsset" or not parse_chart_name(record.name):
            return
        try:
            parsed = obj.parse_as_object()
            payload = getattr(parsed, "m_Script")
            self._charts[record.resource_id] = parse_chart_payload(
                payload, parse_chart_name(record.name)
            )
        except Exception as exc:
            self._charts[record.resource_id] = {
                "status": "failed",
                "difficulty": parse_chart_name(record.name),
                "warnings": [f"{type(exc).__name__}: {exc}"],
            }

    def analyze(self, report: EnumerationReport) -> dict[str, Any]:
        detection = self._detect(report)
        diagnostics: list[str] = []
        track_entries = self._track_entries(report.catalog_key_entries)
        songs: list[dict[str, Any]] = []
        used_resource_ids: set[str] = set()
        matched_entries = 0
        unmatched_entries = 0

        if track_entries:
            songs, matched_entries, unmatched_entries, used_resource_ids = (
                self._associate_songs(report, track_entries)
            )
        elif detection["status"] == "detected":
            _append_diagnostic(
                diagnostics,
                "Addressables track keys are unavailable; song IDs cannot be "
                "associated from this input alone.",
            )

        unassociated = self._unassociated_charts(report, used_resource_ids)
        if songs and any(song["metadata_status"] == "unavailable" for song in songs):
            _append_diagnostic(
                diagnostics,
                "Song titles and artists are not present in scanned Unity objects; "
                "only song IDs from catalog paths are reported.",
            )

        return {
            "adapter": self.name,
            "schema_version": "1.0",
            "detection": detection,
            "summary": {
                "song_count": len(songs),
                "difficulty_count": sum(
                    len(song["difficulties"]) for song in songs
                ),
                "associated_chart_count": sum(
                    1
                    for song in songs
                    for difficulty in song["difficulties"].values()
                    for _asset in difficulty["chart_assets"]
                ),
                "unassociated_chart_count": len(unassociated),
                "parsed_chart_count": sum(
                    1 for item in self._charts.values() if item.get("status") == "parsed"
                ),
                "failed_chart_count": sum(
                    1 for item in self._charts.values() if item.get("status") != "parsed"
                ),
                "track_key_count": len(track_entries),
                "matched_track_key_count": matched_entries,
                "unmatched_track_key_count": unmatched_entries,
            },
            "songs": songs,
            "unassociated_charts": unassociated,
            "export_preset": self._build_export_preset(songs, unassociated),
            "diagnostics": diagnostics,
        }

    def _detect(self, report: EnumerationReport) -> dict[str, Any]:
        charts = [
            obj for obj in report.objects
            if obj.type_name == "TextAsset" and parse_chart_name(obj.name)
        ]
        music = [
            obj for obj in report.objects
            if obj.type_name == "AudioClip"
            and (obj.name or "").lower().startswith("music")
        ]
        illustrations = [
            obj for obj in report.objects
            if obj.type_name in {"Texture2D", "Sprite"}
            and (obj.name or "").lower().startswith("illustration")
        ]
        track_keys = self._track_entries(report.catalog_key_entries)

        evidence = [
            f"{len(charts)} Phigros-style Chart_* TextAsset(s)",
            f"{len(music)} music AudioClip(s)",
            f"{len(illustrations)} Illustration texture/sprite object(s)",
            f"{len(track_keys)} Addressables Assets/Tracks key row(s)",
        ]
        if not charts and not track_keys:
            status, confidence = "not_detected", "none"
        elif charts and track_keys:
            status, confidence = "detected", "high"
        elif charts and music and illustrations:
            status, confidence = "detected", "medium"
        elif charts or track_keys:
            status, confidence = "detected", "low"
        else:
            status, confidence = "not_detected", "none"
        return {
            "status": status,
            "confidence": confidence,
            "evidence": evidence,
            "chart_object_count": len(charts),
            "music_object_count": len(music),
            "illustration_object_count": len(illustrations),
            "track_key_count": len(track_keys),
        }

    @staticmethod
    def _track_entries(key_entries: list[dict[str, Any]]) -> list[dict[str, str]]:
        entries: list[dict[str, str]] = []
        seen: set[tuple[str, str, str, str]] = set()
        for item in key_entries:
            key = item.get("key")
            internal_id = item.get("internal_id")
            if not isinstance(key, str) or not isinstance(internal_id, str):
                continue
            match = TRACK_KEY_RE.match(key.replace("\\", "/"))
            if not match:
                continue
            song_id = match.group("song_id")
            asset = match.group("asset")
            dependency_key = item.get("dependency_key")
            dependency_key = dependency_key if isinstance(dependency_key, str) else ""
            dedupe = (song_id, asset, internal_id, dependency_key)
            if dedupe in seen:
                continue
            seen.add(dedupe)
            provider = item.get("provider_id")
            primary_key = item.get("primary_key")
            entries.append({
                "song_id": song_id,
                "asset": asset,
                "internal_id": internal_id,
                "provider_id": provider if isinstance(provider, str) else "",
                "primary_key": primary_key if isinstance(primary_key, str) else "",
                "dependency_key": dependency_key,
            })
        return entries

    @staticmethod
    def _asset_matches_record(asset: str, record: ObjectRecord) -> bool:
        stem = Path(asset).stem.casefold()
        name = (record.name or "").casefold()
        chart_match = CHART_ASSET_RE.match(asset)
        if chart_match:
            return record.type_name == "TextAsset" and name == stem
        if stem == "music" or stem.startswith("music_"):
            return record.type_name == "AudioClip" and (
                name == stem or name.startswith("music_")
            )
        if stem.startswith("illustration"):
            return record.type_name in {"Texture2D", "Sprite"} and name == stem
        return name == stem

    def _associate_songs(
        self,
        report: EnumerationReport,
        track_entries: list[dict[str, str]],
    ) -> tuple[list[dict[str, Any]], int, int, set[str]]:
        objects_by_base: dict[str, list[ObjectRecord]] = {}
        for record in report.objects:
            base = _basename(record.source_id)
            if base:
                objects_by_base.setdefault(base, []).append(record)

        # Addressables track locations can carry a short internal ID (for
        # example "3" or "a"). Their dependency key points to the bundle
        # location, whose internal ID is the actual bundle path.
        bundle_ids_by_key: dict[str, set[str]] = {}
        for item in report.catalog_key_entries:
            key = item.get("key")
            internal_id = item.get("internal_id")
            if not isinstance(key, str) or not isinstance(internal_id, str):
                continue
            if not _basename(internal_id).endswith(".bundle"):
                continue
            bundle_ids_by_key.setdefault(key, set()).add(internal_id)
            primary_key = item.get("primary_key")
            if isinstance(primary_key, str) and primary_key:
                bundle_ids_by_key.setdefault(primary_key, set()).add(internal_id)

        songs: dict[str, dict[str, Any]] = {}
        matched = 0
        unmatched = 0
        used: set[str] = set()

        for entry in track_entries:
            song = songs.setdefault(entry["song_id"], {
                "song_id": entry["song_id"],
                "title": None,
                "artist": None,
                "metadata_status": "unavailable",
                "difficulties": {},
                "audio": [],
                "illustrations": [],
                "other_assets": [],
                "unmatched_assets": [],
                "diagnostics": [],
            })
            bundle_ids = bundle_ids_by_key.get(entry.get("dependency_key", ""), set())
            bundle_bases = {_basename(value) for value in bundle_ids}
            # Keep compatibility with catalogs where the track location itself
            # directly names the bundle.
            direct_base = _basename(entry["internal_id"])
            if direct_base.endswith(".bundle"):
                bundle_bases.add(direct_base)
            candidates = [
                record
                for base in bundle_bases
                for record in objects_by_base.get(base, [])
                if self._asset_matches_record(entry["asset"], record)
            ]
            if not candidates:
                unmatched += 1
                if len(song["unmatched_assets"]) < MAX_UNMATCHED_ASSETS:
                    song["unmatched_assets"].append({
                        "asset": entry["asset"],
                        "internal_id": entry["internal_id"],
                    })
                _append_diagnostic(
                    song["diagnostics"],
                    f"No scanned object matched catalog asset {entry['asset']!r}.",
                )
                continue

            matched += 1
            for record in candidates:
                used.add(record.resource_id)
                self._add_asset(song, entry, record)
            candidates_chart = CHART_ASSET_RE.match(entry["asset"])
            if candidates_chart:
                chart_records = [
                    record for record in candidates
                    if record.type_name == "TextAsset"
                ]
                if not chart_records:
                    _append_diagnostic(
                        song["diagnostics"],
                        f"Chart asset {entry['asset']!r} matched a non-TextAsset object.",
                    )

        song_list = sorted(songs.values(), key=lambda item: _sort_song_id(item["song_id"]))
        for song in song_list:
            song["difficulties"] = dict(sorted(
                song["difficulties"].items(),
                key=lambda item: (item[1]["sort_order"], item[0]),
            ))
        return song_list, matched, unmatched, used

    def _add_asset(
        self,
        song: dict[str, Any],
        entry: dict[str, str],
        record: ObjectRecord,
    ) -> None:
        asset = entry["asset"]
        chart_match = CHART_ASSET_RE.match(asset)
        if chart_match:
            difficulty = normalize_difficulty(chart_match.group("difficulty"))
            code = difficulty["code"] or "UNKNOWN"
            bucket = song["difficulties"].setdefault(code, {
                **difficulty,
                "chart_assets": [],
                "chart_metadata": None,
            })
            bucket["chart_assets"].append(_resource_ref(record))
            metadata = self._charts.get(record.resource_id)
            if bucket["chart_metadata"] is None and metadata is not None:
                bucket["chart_metadata"] = metadata
            return

        stem = Path(asset).stem.lower()
        if stem == "music" or stem.startswith("music_"):
            if not any(
                item["resource_id"] == record.resource_id for item in song["audio"]
            ):
                song["audio"].append({**_resource_ref(record), "asset": asset})
            return
        if stem.startswith("illustration"):
            if not any(
                item["resource_id"] == record.resource_id
                for item in song["illustrations"]
            ):
                song["illustrations"].append({
                    **_resource_ref(record),
                    "asset": asset,
                    "kind": Path(asset).stem,
                })
            return
        song["other_assets"].append({**_resource_ref(record), "asset": asset})

    def _unassociated_charts(
        self,
        report: EnumerationReport,
        used_resource_ids: set[str],
    ) -> list[dict[str, Any]]:
        charts = []
        for record in report.objects:
            if record.resource_id in used_resource_ids:
                continue
            difficulty = parse_chart_name(record.name)
            if record.type_name != "TextAsset" or difficulty is None:
                continue
            charts.append({
                "resource_id": record.resource_id,
                "type_name": record.type_name,
                "name": record.name,
                "difficulty": difficulty,
                "source_id": record.source_id,
                "container_id": record.container_id,
                "path_id": record.path_id,
                "chart_metadata": self._charts.get(record.resource_id),
            })
        charts.sort(key=lambda item: (item["name"] or "", item["source_id"]))
        return charts

    def _build_export_preset(
        self,
        songs: list[dict[str, Any]],
        unassociated_charts: list[dict[str, Any]],
    ) -> dict[str, Any]:
        entries: list[dict[str, Any]] = []
        used_destinations: set[str] = set()

        def add(
            role: str,
            ref: dict[str, Any],
            destination: str,
            *,
            song_id: str | None = None,
            difficulty: str | None = None,
        ) -> None:
            unique = destination
            index = 2
            while unique in used_destinations:
                path = Path(destination)
                unique = str(path.with_name(f"{path.stem}_{index}{path.suffix}"))
                index += 1
            used_destinations.add(unique)
            entries.append({
                "role": role,
                "song_id": song_id,
                "difficulty": difficulty,
                "resource_id": ref["resource_id"],
                "type_name": ref["type_name"],
                "name": ref["name"],
                "source_id": ref["source_id"],
                "path_id": ref["path_id"],
                "destination": unique,
            })

        for song in songs:
            song_segment = _safe_segment(song["song_id"], "song")
            for difficulty in song["difficulties"].values():
                code = (difficulty["code"] or "unknown").lower()
                for asset in difficulty["chart_assets"]:
                    add(
                        "chart",
                        asset,
                        f"phigros/songs/{song_segment}/charts/{code}.json",
                        song_id=song["song_id"],
                        difficulty=difficulty["code"],
                    )
            for asset in song["audio"]:
                suffix = _asset_suffix(asset["asset"]) or ".wav"
                add(
                    "audio",
                    asset,
                    f"phigros/songs/{song_segment}/audio/"
                    f"{_safe_segment(Path(asset['asset']).stem or asset['name'] or 'music')}{suffix}",
                    song_id=song["song_id"],
                )
            for asset in song["illustrations"]:
                add(
                    "illustration",
                    asset,
                    f"phigros/songs/{song_segment}/illustrations/"
                    f"{_safe_segment(asset['kind'] or asset['name'] or 'illustration')}.png",
                    song_id=song["song_id"],
                )

        for chart in unassociated_charts:
            add(
                "chart",
                chart,
                "phigros/unassociated/charts/"
                f"{_safe_segment(chart['name'])}__{chart['resource_id'][:10]}.json",
                difficulty=(chart["difficulty"] or {}).get("code"),
            )

        return {
            "schema_version": "1.0",
            "name": "phigros",
            "mode": "plan",
            "root": "phigros",
            "entry_count": len(entries),
            "entries": entries,
            "notes": [
                "This preset is a deterministic export plan.",
                "Resource payloads remain owned by the generic exporter.",
            ],
        }
