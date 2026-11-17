"""Bounded P1 discovery and Unity object enumeration."""
from __future__ import annotations

import hashlib
import os
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Iterable

from .backends.unitypy import UnityPyBackend
from .object_models import EnumerationReport, ObjectRecord, ObjectReference
from .scanner import BUNDLE_MAGICS, _looks_like_serialized_header

MAX_CONTAINERS = 20_000
MAX_CONTAINER_BYTES = 512 * 1024 * 1024
MAX_TOTAL_CONTAINER_BYTES = 2 * 1024 * 1024 * 1024
MAX_OBJECTS = 1_000_000
MAX_REFERENCES = 2_000_000
MAX_COMPRESSION_RATIO = 1_000
COPY_CHUNK_SIZE = 1024 * 1024


def _is_candidate(prefix: bytes) -> bool:
    return any(prefix.startswith(magic) for magic in BUNDLE_MAGICS) or _looks_like_serialized_header(prefix)


def _identity(source_id: str, container_id: str, path_id: int) -> str:
    raw = f"{source_id}\0{container_id}\0{path_id}".encode("utf-8", errors="surrogatepass")
    return hashlib.sha256(raw).hexdigest()


def _safe_attr(obj: Any, name: str, default: Any = None) -> Any:
    try:
        return getattr(obj, name)
    except Exception:
        return default


class UnityObjectEnumerator:
    """Enumerate metadata without decoding/exporting object payloads.

    ZIP/APK members are selected by their binary headers and copied to uniquely
    named temporary files. Archive paths are never used as filesystem paths.
    """

    def __init__(
        self,
        backend: Any | None = None,
        max_container_bytes: int = MAX_CONTAINER_BYTES,
        max_total_bytes: int = MAX_TOTAL_CONTAINER_BYTES,
        max_objects: int = MAX_OBJECTS,
        include_references: bool = False,
        max_references: int = MAX_REFERENCES,
        object_callback: Any | None = None,
    ) -> None:
        self.backend = backend or UnityPyBackend()
        self.max_container_bytes = max_container_bytes
        self.max_total_bytes = max_total_bytes
        self.max_objects = max_objects
        self.include_references = include_references
        self.max_references = max_references
        self.object_callback = object_callback

    def enumerate(self, input_path: str | os.PathLike[str]) -> EnumerationReport:
        path = Path(input_path).expanduser()
        report = EnumerationReport(
            schema_version="1.0",
            input_path=str(path),
            parser_name=getattr(self.backend, "name", type(self.backend).__name__),
            parser_version=getattr(self.backend, "version", None),
        )
        if not path.exists():
            report.scan_status = "failed"
            report.errors.append("Input path does not exist or is not accessible.")
            return report

        try:
            if path.is_dir():
                self._enumerate_directory(path, report)
            elif path.is_file() and zipfile.is_zipfile(path):
                self._enumerate_archive(path, report)
            elif path.is_file():
                size = path.stat().st_size
                if size > self.max_container_bytes:
                    report.warnings.append(f"Skipped oversized standalone input ({size} bytes).")
                else:
                    self._enumerate_file(path, str(path.resolve()), report)
            else:
                report.scan_status = "failed"
                report.errors.append("Input is neither a regular file nor a directory.")
        except Exception as exc:
            report.errors.append(f"{type(exc).__name__}: {exc}")

        if report.errors and report.objects:
            report.scan_status = "partial"
        elif report.errors and not report.objects:
            report.scan_status = "failed"
        elif not report.inspected_containers:
            report.warnings.append("No supported SerializedFile or AssetBundle candidates were found.")
        return report

    def _enumerate_directory(self, root: Path, report: EnumerationReport) -> None:
        total_bytes = 0
        for current, dirs, names in os.walk(root):
            dirs[:] = [name for name in dirs if name not in {".git", "__pycache__"}]
            for name in names:
                if report.inspected_containers >= MAX_CONTAINERS:
                    report.warnings.append(f"Container limit reached ({MAX_CONTAINERS}).")
                    return
                source = Path(current) / name
                try:
                    size = source.stat().st_size
                    with source.open("rb") as stream:
                        prefix = stream.read(32)
                    if not _is_candidate(prefix):
                        continue
                    if size > self.max_container_bytes:
                        report.warnings.append(f"Skipped oversized candidate: {source.relative_to(root).as_posix()}")
                        continue
                    if total_bytes + size > self.max_total_bytes:
                        report.warnings.append("Total container byte limit reached; remaining candidate files were not inspected.")
                        return
                    total_bytes += size
                    self._enumerate_file(source, f"{root.resolve()}!/{source.relative_to(root).as_posix()}", report)
                except OSError as exc:
                    report.warnings.append(f"Could not inspect {source}: {exc}")

    def _enumerate_archive(self, archive_path: Path, report: EnumerationReport) -> None:
        total_bytes = 0
        with zipfile.ZipFile(archive_path) as archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                if report.inspected_containers >= MAX_CONTAINERS:
                    report.warnings.append(f"Container limit reached ({MAX_CONTAINERS}).")
                    return
                logical_name = info.filename.replace("\\", "/")
                try:
                    # Inspect only a tiny prefix before applying candidate limits,
                    # so unrelated APK entries cannot consume the candidate budget.
                    with archive.open(info) as stream:
                        prefix = stream.read(32)
                    if not _is_candidate(prefix):
                        continue
                    if info.file_size > self.max_container_bytes:
                        report.warnings.append(f"Skipped oversized archive member: {logical_name}")
                        continue
                    if info.compress_size and info.file_size / info.compress_size > MAX_COMPRESSION_RATIO:
                        report.warnings.append(f"Skipped suspicious compression ratio: {logical_name}")
                        continue
                    if total_bytes + info.file_size > self.max_total_bytes:
                        report.warnings.append("Total container byte limit reached; remaining candidate archive members were not inspected.")
                        return
                    total_bytes += info.file_size
                    self._enumerate_archive_member(archive_path, info, logical_name, report)
                except Exception as exc:
                    # Isolate malformed members and parser failures; keep scanning siblings.
                    report.errors.append(f"{logical_name}: {type(exc).__name__}: {exc}")

    def _enumerate_archive_member(
        self,
        archive_path: Path,
        info: zipfile.ZipInfo,
        logical_name: str,
        report: EnumerationReport,
    ) -> None:
        suffix = Path(logical_name).suffix[:16]
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(prefix="unityextractor-", suffix=suffix, delete=False) as target:
                temp_path = Path(target.name)
                copied = 0
                with zipfile.ZipFile(archive_path).open(info) as source:
                    while True:
                        chunk = source.read(COPY_CHUNK_SIZE)
                        if not chunk:
                            break
                        copied += len(chunk)
                        if copied > self.max_container_bytes:
                            raise ValueError("Archive member exceeded configured extraction limit.")
                        target.write(chunk)
            self._enumerate_file(temp_path, f"{archive_path}!/{logical_name}", report)
        finally:
            if temp_path is not None:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError as exc:
                    report.warnings.append(f"Could not remove temporary parser input: {exc}")

    def _enumerate_file(self, source: Path, logical_source_id: str, report: EnumerationReport) -> None:
        try:
            env = self.backend.load(source)
            report.inspected_containers += 1
        except Exception as exc:
            report.errors.append(f"{logical_source_id}: parser failed: {type(exc).__name__}: {exc}")
            return

        try:
            objects: Iterable[Any] = env.objects
            for obj in objects:
                if len(report.objects) >= self.max_objects:
                    report.warnings.append(f"Object limit reached ({self.max_objects}).")
                    return
                record = self._append_object(obj, logical_source_id, report)
                if record is not None and self.object_callback is not None:
                    try:
                        self.object_callback(obj, record)
                    except Exception as exc:
                        warning = f"Resource export failed: {type(exc).__name__}: {exc}"
                        record.warnings.append(warning)
                        report.warnings.append(f"{logical_source_id} / Path ID {record.path_id}: {warning}")
                if record is not None and self.include_references:
                    self._append_references(obj, record, logical_source_id, report)
        except Exception as exc:
            report.errors.append(f"{logical_source_id}: object iteration failed: {type(exc).__name__}: {exc}")

    def _append_object(self, obj: Any, logical_source_id: str, report: EnumerationReport) -> ObjectRecord | None:
        path_id = _safe_attr(obj, "path_id")
        try:
            path_id = int(path_id)
        except (TypeError, ValueError):
            report.errors.append(f"{logical_source_id}: skipped object without a valid Path ID.")
            return None

        assets_file = _safe_attr(obj, "assets_file")
        asset_name = _safe_attr(assets_file, "name") or "unknown-serialized-file"
        # UnityPy's parent.name can be the random temporary filename used
        # when staging an APK member. It is not stable across runs. The
        # serialized-file name is the stable inner-container identifier;
        # logical_source_id already distinguishes different outer APK members.
        container_id = str(asset_name) if asset_name else logical_source_id

        obj_type = _safe_attr(obj, "type")
        type_name = _safe_attr(obj_type, "name") or str(obj_type or "unknown")
        try:
            size_bytes = int(_safe_attr(obj, "byte_size"))
        except (TypeError, ValueError):
            size_bytes = None

        warnings: list[str] = []
        name: str | None = None
        try:
            peek_name = getattr(obj, "peek_name")
            value = peek_name()
            name = str(value) if value is not None else None
        except Exception as exc:
            warnings.append(f"Name unavailable: {type(exc).__name__}: {exc}")

        container_path: str | None = None
        try:
            value = getattr(obj, "container")
            container_path = str(value) if value else None
        except Exception as exc:
            warnings.append(f"Container path unavailable: {type(exc).__name__}: {exc}")

        header = _safe_attr(assets_file, "header")
        metadata = {
            "class_id": _safe_attr(obj, "class_id"),
            "type_id": _safe_attr(obj, "type_id"),
            "serialized_file_version": _safe_attr(header, "version"),
            "unity_version": _safe_attr(assets_file, "unity_version"),
        }
        metadata = {key: value for key, value in metadata.items() if isinstance(value, (str, int, float, bool)) or value is None}
        record = ObjectRecord(
            resource_id=_identity(logical_source_id, container_id, path_id),
            source_id=logical_source_id,
            container_id=container_id,
            path_id=path_id,
            type_name=str(type_name),
            name=name,
            size_bytes=size_bytes,
            container_path=container_path,
            metadata=metadata,
            warnings=warnings,
        )
        report.objects.append(record)
        if warnings:
            report.warnings.extend(f"{logical_source_id} / Path ID {path_id}: {warning}" for warning in warnings)
        return record

    def _append_references(
        self,
        obj: Any,
        source_record: ObjectRecord,
        logical_source_id: str,
        report: EnumerationReport,
    ) -> None:
        iterator = getattr(self.backend, "iter_references", None)
        if not callable(iterator):
            warning = "Reference analysis is not supported by the selected parser backend."
            source_record.warnings.append(warning)
            report.warnings.append(f"{logical_source_id} / Path ID {source_record.path_id}: {warning}")
            return
        try:
            for file_id, target_path_id, target_container_id, resolved in iterator(obj):
                if len(report.references) >= self.max_references:
                    warning = f"Reference limit reached ({self.max_references})."
                    if warning not in report.warnings:
                        report.warnings.append(warning)
                    return
                target_id = (
                    _identity(logical_source_id, target_container_id, int(target_path_id))
                    if resolved and target_container_id
                    else None
                )
                report.references.append(
                    ObjectReference(
                        source_resource_id=source_record.resource_id,
                        target_resource_id=target_id,
                        target_container_id=target_container_id,
                        target_path_id=int(target_path_id),
                        file_id=int(file_id),
                        resolved=bool(resolved and target_id),
                    )
                )
        except Exception as exc:
            warning = f"Reference parsing failed: {type(exc).__name__}: {exc}"
            source_record.warnings.append(warning)
            report.warnings.append(f"{logical_source_id} / Path ID {source_record.path_id}: {warning}")
