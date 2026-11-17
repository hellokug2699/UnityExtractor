"""Bounded, non-extracting scanner for Unity package evidence.

The scanner deliberately avoids loading or executing package code. ZIP/APK entries
are inspected in-place and only a bounded prefix of selected files is read.
"""
from __future__ import annotations

import os
import re
import struct
import zipfile
from pathlib import Path
from typing import Iterable

from .models import Evidence, Finding, ScanReport

MAX_ENTRIES = 100_000
MAX_ENTRY_READ = 2 * 1024 * 1024
MAX_TOTAL_READ = 64 * 1024 * 1024
MAX_RATIO = 1_000
UNITY_VERSION_RE = re.compile(rb"\b(20\d{2}\.[0-3]\.\d{1,3}[abfp]\d{1,4})\b")
BUNDLE_MAGICS = (b"UnityFS", b"UnityWeb", b"UnityRaw")


def _safe_name(name: str) -> str:
    return name.replace("\\", "/").lstrip("./")


def _looks_like_serialized_header(data: bytes) -> bool:
    """Conservative structural heuristic for Unity SerializedFile headers."""
    if len(data) < 20:
        return False
    try:
        metadata_size, file_size, version, data_offset = struct.unpack(">IIII", data[:16])
    except struct.error:
        return False
    if not (5 <= version <= 30):
        return False
    if not (20 <= metadata_size <= file_size <= 2**40):
        return False
    return 16 <= data_offset <= file_size


def _read_prefix(path: Path, limit: int = MAX_ENTRY_READ) -> bytes:
    with path.open("rb") as stream:
        return stream.read(limit)


class UnityScanner:
    def scan(self, input_path: str | os.PathLike[str]) -> ScanReport:
        path = Path(input_path).expanduser()
        report = ScanReport(
            schema_version="1.0",
            input_path=str(path),
            input_kind=self._input_kind(path),
        )
        try:
            if path.is_dir():
                self._scan_directory(path, report)
            elif path.is_file() and zipfile.is_zipfile(path):
                self._scan_archive(path, report)
            elif path.is_file():
                self._scan_single_file(path, report)
            else:
                report.scan_status = "failed"
                report.errors.append("Input path does not exist or is not accessible.")
                return report
        except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
            report.scan_status = "partial" if report.evidence else "failed"
            report.errors.append(f"{type(exc).__name__}: {exc}")

        self._finalize(report)
        return report

    @staticmethod
    def _input_kind(path: Path) -> str:
        if path.is_dir():
            return "directory"
        if path.suffix.lower() == ".apk":
            return "apk"
        if path.suffix.lower() == ".zip":
            return "zip"
        return "file"

    def _scan_directory(self, root: Path, report: ScanReport) -> None:
        total_read = 0
        for current, dirs, names in os.walk(root):
            dirs[:] = [d for d in dirs if d not in {".git", "__pycache__"}]
            for name in names:
                if report.inspected_entries >= MAX_ENTRIES:
                    report.warnings.append(f"Entry limit reached ({MAX_ENTRIES}).")
                    return
                file_path = Path(current) / name
                rel = file_path.relative_to(root).as_posix()
                self._inspect_name(rel, report)
                try:
                    size = file_path.stat().st_size
                    if size > MAX_ENTRY_READ or total_read >= MAX_TOTAL_READ:
                        continue
                    data = _read_prefix(file_path, min(MAX_ENTRY_READ, MAX_TOTAL_READ - total_read))
                    total_read += len(data)
                    self._inspect_bytes(rel, data, report)
                    report.inspected_entries += 1
                except OSError as exc:
                    report.warnings.append(f"Could not inspect {rel}: {exc}")

    def _scan_archive(self, path: Path, report: ScanReport) -> None:
        total_read = 0
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_ENTRIES:
                report.warnings.append(f"Archive has {len(infos)} entries; inspecting first {MAX_ENTRIES}.")
            for info in infos[:MAX_ENTRIES]:
                name = _safe_name(info.filename)
                self._inspect_name(name, report)
                if info.is_dir():
                    continue
                if info.file_size > MAX_ENTRY_READ:
                    report.warnings.append(f"Skipped oversized entry: {name}")
                    continue
                if info.compress_size and info.file_size / info.compress_size > MAX_RATIO:
                    report.warnings.append(f"Skipped suspicious compression ratio: {name}")
                    continue
                if total_read >= MAX_TOTAL_READ:
                    report.warnings.append(f"Total read limit reached ({MAX_TOTAL_READ} bytes).")
                    break
                try:
                    with archive.open(info) as stream:
                        data = stream.read(min(MAX_ENTRY_READ, MAX_TOTAL_READ - total_read))
                    total_read += len(data)
                    self._inspect_bytes(name, data, report)
                    report.inspected_entries += 1
                except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
                    report.warnings.append(f"Could not read {name}: {type(exc).__name__}: {exc}")

    def _scan_single_file(self, path: Path, report: ScanReport) -> None:
        name = path.name
        self._inspect_name(name, report)
        size = path.stat().st_size
        if size > MAX_ENTRY_READ:
            report.warnings.append(f"Only the first {MAX_ENTRY_READ} bytes of the standalone file can be inspected.")
        data = _read_prefix(path)
        self._inspect_bytes(name, data, report)
        report.inspected_entries = 1

    def _inspect_name(self, name: str, report: ScanReport) -> None:
        normalized = _safe_name(name).lower()
        base = normalized.rsplit("/", 1)[-1]
        if base in {"globalgamemanagers", "globalgamemanagers.assets", "resources.assets"}:
            self._add(report, "unity_data_file", f"Unity data filename: {name}", name, "strong")
        if base in {"unityplayer.dll", "libunity.so", "libunity.dylib"}:
            self._add(report, "unity_runtime", f"Unity runtime library: {name}", name, "strong")
        if base == "global-metadata.dat":
            self._add(report, "il2cpp_metadata", "IL2CPP global metadata filename found.", name, "supporting")
        if base == "libil2cpp.so" or base == "gameassembly.dll":
            self._add(report, "il2cpp_runtime", f"IL2CPP runtime binary: {name}", name, "strong")
        if base == "mono" or (base.endswith(".dll") and "/managed/" in f"/{normalized}/" and base != "global-metadata.dat"):
            self._add(report, "mono_managed", f"Mono/Managed directory or assembly candidate: {name}", name, "supporting")
        if "streamingassets" in normalized:
            self._add(report, "streaming_assets", f"StreamingAssets path candidate: {name}", name, "supporting")
        if base.endswith(".json") and ("catalog" in base or "settings" in base):
            self._add(report, "addressables_catalog_candidate", f"Addressables catalog filename candidate: {name}", name, "weak")
        if base == "data.unity3d":
            self._add(report, "asset_bundle_candidate", f"Common Unity bundle filename: {name}", name, "supporting")

    def _inspect_bytes(self, name: str, data: bytes, report: ScanReport) -> None:
        if not data:
            return
        if any(data.startswith(magic) for magic in BUNDLE_MAGICS):
            magic = next(m.decode("ascii") for m in BUNDLE_MAGICS if data.startswith(m))
            self._add(report, "asset_bundle", f"{magic} bundle signature found.", name, "strong")
        if _looks_like_serialized_header(data):
            self._add(report, "serialized_file_candidate", "Plausible Unity SerializedFile header.", name, "supporting")
        match = UNITY_VERSION_RE.search(data[:MAX_ENTRY_READ])
        if match:
            version = match.group(1).decode("ascii", errors="replace")
            self._add(report, "unity_version_string", f"Unity version string found: {version}", name, "strong", value=version)
        lowered = data[:MAX_ENTRY_READ].lower()
        if b"m_assemblyname" in lowered or b"assembly-csharp" in lowered:
            self._add(report, "mono_metadata_clue", "Managed assembly metadata clue found.", name, "supporting")
        if b"m_assemblyname" in lowered and b"il2cpp" in lowered:
            self._add(report, "mixed_runtime_clues", "Conflicting or mixed runtime-related strings found.", name, "weak")

    @staticmethod
    def _add(report: ScanReport, code: str, description: str, source: str | None, strength: str, value: str | None = None) -> None:
        if any(e.code == code and e.source == source and e.description == description for e in report.evidence):
            return
        report.evidence.append(Evidence(code, description, source, strength))
        if value is not None and code == "unity_version_string":
            # Version is selected during finalization from explicit evidence only.
            pass

    def _finalize(self, report: ScanReport) -> None:
        codes = {item.code for item in report.evidence}
        strong_unity = {"unity_data_file", "unity_runtime", "asset_bundle"}
        unity_hits = codes & strong_unity
        if unity_hits:
            report.engine_name = "Unity"
            report.engine_confidence = "high" if len(unity_hits) >= 2 or "asset_bundle" in unity_hits else "medium"
        elif codes & {"serialized_file_candidate", "streaming_assets", "il2cpp_metadata"}:
            report.engine_name = "Unity"
            report.engine_confidence = "low"

        versions = []
        for item in report.evidence:
            if item.code == "unity_version_string":
                match = re.search(r"(20\d{2}\.[0-3]\.\d{1,3}[abfp]\d{1,4})", item.description)
                if match and match.group(1) not in versions:
                    versions.append(match.group(1))
        if len(versions) == 1:
            report.unity_version = versions[0]
            report.unity_version_confidence = "medium"
        elif len(versions) > 1:
            report.warnings.append("Multiple Unity version strings found; version is left unknown.")

        has_il2cpp = "il2cpp_runtime" in codes or "il2cpp_metadata" in codes
        has_mono = "mono_managed" in codes or "mono_metadata_clue" in codes
        if has_il2cpp and not has_mono:
            report.scripting_backend = "IL2CPP"
        elif has_mono and not has_il2cpp:
            report.scripting_backend = "Mono"
        elif has_mono and has_il2cpp:
            report.scripting_backend = "unknown"
            report.warnings.append("Both Mono and IL2CPP clues were found; backend remains unknown.")

        grouped: dict[str, list[Evidence]] = {}
        for item in report.evidence:
            kind = self._finding_kind(item.code)
            if kind:
                grouped.setdefault(kind, []).append(item)
        report.findings = [
            Finding(kind, self._finding_status(kind, entries), entries, len(entries))
            for kind, entries in sorted(grouped.items())
        ]

    @staticmethod
    def _finding_kind(code: str) -> str | None:
        mapping = {
            "unity_data_file": "unity_player_data",
            "unity_runtime": "unity_runtime",
            "asset_bundle": "asset_bundle",
            "asset_bundle_candidate": "asset_bundle",
            "serialized_file_candidate": "serialized_file",
            "addressables_catalog_candidate": "addressables",
            "streaming_assets": "streaming_assets",
            "il2cpp_runtime": "il2cpp",
            "il2cpp_metadata": "il2cpp",
            "mono_managed": "mono",
            "mono_metadata_clue": "mono",
        }
        return mapping.get(code)

    @staticmethod
    def _finding_status(kind: str, entries: Iterable[Evidence]) -> str:
        strengths = {entry.strength for entry in entries}
        return "detected" if "strong" in strengths else "candidate"
