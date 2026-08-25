"""Local archive inventory, snapshot, and transfer primitives."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import stat
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Iterator
from uuid import uuid4

from dicepp_data import (
    DATA_CATALOG,
    DataAssetKind,
    InstanceLayout,
)

from dicepp_meta import get_version as get_dicepp_version

ARCHIVE_FORMAT_VERSION = 1
MANIFEST_NAME = "manifest.json"
MAX_ARCHIVE_BYTES = 16 * 1024**3
MAX_MEMBER_BYTES = 8 * 1024**3
MAX_TOTAL_UNCOMPRESSED_BYTES = 32 * 1024**3
MAX_MEMBER_COUNT = 100_000
MAX_MANIFEST_BYTES = 2 * 1024**2
SUPPORTED_ZIP_COMPRESSION = {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}


@dataclass(frozen=True)
class ArchivePayload:
    """A local filesystem file and its POSIX archive name."""

    path: Path
    arcname: str


class ArchiveError(Exception):
    """Raised when an archive cannot be created safely."""


class ArchiveNameError(ArchiveError):
    """Raised when a requested archive filename is not safe."""


class ArchiveNotFoundError(ArchiveError):
    """Raised when a requested archive does not exist as a regular zip file."""


class ArchiveInvalidError(ArchiveError):
    """Raised when an archive zip cannot provide a manifest."""


def backups_dir(layout: InstanceLayout) -> Path:
    """Return the instance archive inventory directory."""
    return layout.backups_dir


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _format_created_at(now: datetime) -> str:
    return now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _filename_timestamp(now: datetime) -> str:
    return now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def sanitize_description_slug(description: str | None) -> str:
    """Return a filename-safe slug derived from a user description."""
    if not description:
        return ""
    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", description.strip().lower())
    slug = slug.strip("._-")
    return slug[:48].strip("._-")


def _safe_arcname(arcname: str) -> str:
    posix = PurePosixPath(arcname)
    if posix.is_absolute() or ".." in posix.parts or not arcname or "\\" in arcname:
        raise ArchiveError(f"Unsafe archive path: {arcname!r}")
    return posix.as_posix()


def collect_archive_payloads(layout: InstanceLayout) -> list[ArchivePayload]:
    """Collect files included in the complete manual backup."""
    return [
        ArchivePayload(path=match.path, arcname=_safe_arcname(match.logical_path))
        for match in DATA_CATALOG.collect(layout)
    ]


def _checkpoint_managed_sqlite_assets(layout: InstanceLayout) -> None:
    """Fold every catalogued SQLite WAL into its main database before snapshotting.

    Archive creation only stores the main ``.db`` payload.  After the Runtime has
    stopped, a successful truncate checkpoint makes that payload a complete
    snapshot while keeping the archive format independent from SQLite sidecars.
    """
    for match in DATA_CATALOG.collect(layout):
        asset = DATA_CATALOG.find_for_logical_path(match.logical_path)
        if asset is None or asset.kind is not DataAssetKind.SQLITE:
            continue
        try:
            connection = sqlite3.connect(match.path, timeout=5)
            try:
                checkpoint = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            finally:
                connection.close()
        except sqlite3.Error as exc:
            raise ArchiveError(
                f"SQLite checkpoint failed for {match.logical_path}: {exc}"
            ) from exc
        if not isinstance(checkpoint, tuple) or not checkpoint or checkpoint[0] != 0:
            raise ArchiveError(
                f"SQLite checkpoint did not complete for {match.logical_path}"
            )


def _archive_filename(now: datetime, description: str | None) -> str:
    slug = sanitize_description_slug(description)
    suffix = f"-{slug}" if slug else ""
    return f"{_filename_timestamp(now)}{suffix}-{uuid4().hex[:8]}.zip"


def _build_manifest(
    *,
    created_at: str,
    description: str,
    files: list[dict],
) -> dict:
    return {
        "format_version": ARCHIVE_FORMAT_VERSION,
        "created_at": created_at,
        "dicepp_version": get_dicepp_version(),
        "description": description,
        "files": sorted(
            [
                {
                    "path": item["path"],
                    "size": item["size"],
                    "sha256": item["sha256"],
                }
                for item in files
            ],
            key=lambda row: row["path"],
        ),
    }


def _open_regular_payload(path: Path):
    """Open one catalogued regular file for archive streaming."""
    if path.is_symlink() or not path.is_file():
        return None
    try:
        return path.open("rb")
    except (FileNotFoundError, OSError):
        return None


def _write_payload_to_archive(
    archive: zipfile.ZipFile,
    payload: ArchivePayload,
) -> tuple[str, int] | None:
    """Write one payload and return the digest and size of the bytes written."""
    source = _open_regular_payload(payload.path)
    if source is None:
        return None

    digest = hashlib.sha256()
    size = 0
    with source:
        with archive.open(payload.arcname, "w") as target:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
                size += len(chunk)
                target.write(chunk)
    return digest.hexdigest(), size


def create_archive(
    description: str | None = None,
    *,
    layout: InstanceLayout,
) -> tuple[dict, dict]:
    """Create one complete manual backup and return ``(summary, manifest)``."""
    target_dir = backups_dir(layout)
    target_dir.mkdir(parents=True, exist_ok=True)
    now = _utc_now()
    filename = _archive_filename(now, description)
    target = target_dir / filename
    tmp = target.with_name(f"{target.name}.inprogress")
    _checkpoint_managed_sqlite_assets(layout)
    payloads = collect_archive_payloads(layout)
    file_records: list[dict] = []
    manifest: dict | None = None

    try:
        with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for payload in payloads:
                written = _write_payload_to_archive(archive, payload)
                if written is None:
                    raise ArchiveError(
                        f"Archive payload cannot be read safely: {payload.arcname}"
                    )
                checksum, size = written
                if DATA_CATALOG.find_for_logical_path(payload.arcname) is None:
                    raise ArchiveError(
                        f"Collected payload is not owned by the catalog: {payload.arcname}"
                    )
                file_records.append(
                    {
                        "path": payload.arcname,
                        "size": size,
                        "sha256": checksum,
                    }
                )
            manifest = _build_manifest(
                created_at=_format_created_at(now),
                description=description or "",
                files=file_records,
            )
            archive.writestr(
                MANIFEST_NAME,
                json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
            )
        # Verify the exact bytes written before making the archive observable.
        verification = verify_archive_path(tmp, expected_filename=target.name)
        if not verification["verified"]:
            raise ArchiveInvalidError(
                "New archive verification failed: " + "; ".join(verification["problems"])
            )
        tmp.replace(target)
    except Exception:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
        raise

    summary = archive_summary(target)
    if manifest is None:
        raise ArchiveError("Archive manifest was not written")
    return summary, manifest


def safe_archive_path(
    filename: str,
    *,
    layout: InstanceLayout,
) -> Path:
    """Return the backups-dir path for a safe archive filename."""
    if not filename or filename != Path(filename).name:
        raise ArchiveNameError("Archive filename must not contain a path")
    if filename != PurePosixPath(filename).name:
        raise ArchiveNameError("Archive filename must not contain a path")
    if (
        filename in {".", ".."}
        or ".." in Path(filename).parts
        or ".." in PurePosixPath(filename).parts
    ):
        raise ArchiveNameError("Archive filename must not contain '..'")
    if Path(filename).is_absolute() or PurePosixPath(filename).is_absolute():
        raise ArchiveNameError("Archive filename must not be absolute")
    if Path(filename).suffix.lower() != ".zip":
        raise ArchiveNameError("Archive filename must end with .zip")

    return backups_dir(layout) / filename


def _existing_regular_archive_path(
    filename: str,
    *,
    layout: InstanceLayout,
) -> Path:
    path = safe_archive_path(filename, layout=layout)
    if path.is_symlink() or not path.is_file():
        raise ArchiveNotFoundError(f"Archive not found: {filename}")
    return path


@contextmanager
def _open_existing_archive(
    filename: str,
    *,
    layout: InstanceLayout,
) -> Iterator[tuple[Path, object, zipfile.ZipFile]]:
    """Open one regular archive file for inspection."""
    path = _existing_regular_archive_path(
        filename,
        layout=layout,
    )
    try:
        archive = zipfile.ZipFile(path, "r")
    except zipfile.BadZipFile as exc:
        raise ArchiveInvalidError("Archive zip cannot be read") from exc
    with archive:
        yield path, path.stat(), archive


def archive_summary(path: Path) -> dict:
    """Return a robust summary for one zip file."""
    stat = path.stat()
    fallback_created_at = _format_created_at(datetime.fromtimestamp(stat.st_mtime, timezone.utc))
    summary = {
        "filename": path.name,
        "size": stat.st_size,
        "created_at": fallback_created_at,
        "valid": False,
    }
    try:
        with zipfile.ZipFile(path, "r") as archive:
            if _validate_zip_structure(archive):
                return summary
            manifest = _read_manifest_from_open_archive(archive)
            files = _manifest_files(manifest)
    except (OSError, ArchiveError, KeyError, json.JSONDecodeError, zipfile.BadZipFile):
        return summary

    if isinstance(manifest, dict):
        summary["valid"] = True
        summary["created_at"] = manifest.get("created_at") or fallback_created_at
        summary["description"] = manifest.get("description", "")
        summary["format_version"] = manifest.get("format_version")
        summary["dicepp_version"] = manifest.get("dicepp_version", "")
        summary["file_count"] = len(files)
    return summary


def _archive_summary_from_manifest(
    path: Path,
    stat_info: object,
    manifest: dict,
) -> dict:
    files = _manifest_files(manifest)
    fallback_created_at = _format_created_at(
        datetime.fromtimestamp(stat_info.st_mtime, timezone.utc)
    )
    return {
        "filename": path.name,
        "size": stat_info.st_size,
        "created_at": manifest.get("created_at") or fallback_created_at,
        "valid": True,
        "description": manifest.get("description", ""),
        "format_version": manifest.get("format_version"),
        "dicepp_version": manifest.get("dicepp_version", ""),
        "file_count": len(files),
    }


def _read_manifest_from_open_archive(archive: zipfile.ZipFile) -> dict:
    try:
        info = archive.getinfo(MANIFEST_NAME)
        if info.file_size > MAX_MANIFEST_BYTES:
            raise ArchiveInvalidError("Archive manifest exceeds size limit")
        with archive.open(info) as handle:
            raw = handle.read(MAX_MANIFEST_BYTES + 1)
        if len(raw) > MAX_MANIFEST_BYTES:
            raise ArchiveInvalidError("Archive manifest exceeds size limit")
        manifest = json.loads(raw)
    except KeyError as exc:
        raise ArchiveInvalidError("Archive manifest is missing") from exc
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, zipfile.BadZipFile) as exc:
        raise ArchiveInvalidError("Archive manifest cannot be read") from exc

    if not isinstance(manifest, dict):
        raise ArchiveInvalidError("Archive manifest must be a JSON object")
    return manifest


def read_archive_detail(
    filename: str,
    *,
    layout: InstanceLayout,
) -> tuple[dict, dict]:
    """Return ``(summary, manifest)`` for a regular archive zip filename."""
    with _open_existing_archive(
        filename,
        layout=layout,
    ) as (path, stat_info, archive):
        structure = _validate_zip_structure(archive)
        if structure:
            raise ArchiveInvalidError("; ".join(structure))
        manifest = _read_manifest_from_open_archive(archive)
        return (
            _archive_summary_from_manifest(path, stat_info, manifest),
            manifest,
        )


def _manifest_files(manifest: dict) -> list[dict]:
    format_version = manifest.get("format_version")
    if format_version != ARCHIVE_FORMAT_VERSION:
        raise ArchiveInvalidError("Unsupported archive format version")

    files = manifest.get("files")
    if not isinstance(files, list):
        raise ArchiveInvalidError("Archive manifest files must be an array")
    if any(
        not isinstance(item, dict)
        or not isinstance(item.get("path"), str)
        or not isinstance(item.get("size"), int)
        or item["size"] < 0
        or not isinstance(item.get("sha256"), str)
        for item in files
    ):
        raise ArchiveInvalidError("Archive manifest contains an invalid file record")
    if len({item["path"] for item in files}) != len(files):
        raise ArchiveInvalidError("Archive manifest contains duplicate file records")
    return files


def _is_safe_manifest_arcname(arcname: str) -> bool:
    posix = PurePosixPath(arcname)
    return (
        bool(arcname)
        and "\\" not in arcname
        and not posix.is_absolute()
        and ".." not in posix.parts
    )


def _sha256_zip_member(archive: zipfile.ZipFile, arcname: str) -> str:
    digest = hashlib.sha256()
    with archive.open(arcname, "r") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _zip_member_is_regular(info: zipfile.ZipInfo) -> bool:
    mode = (info.external_attr >> 16) & 0xFFFF
    if stat.S_IFMT(mode) == 0:
        return not info.is_dir()
    return stat.S_ISREG(mode)


def _validate_zip_structure(archive: zipfile.ZipFile) -> list[str]:
    problems: list[str] = []
    infos = archive.infolist()
    if len(infos) > MAX_MEMBER_COUNT:
        problems.append(f"Archive contains too many members: {len(infos)}")
        return problems
    seen: set[str] = set()
    total = 0
    for info in infos:
        name = info.filename
        if name in seen:
            problems.append(f"Duplicate zip member: {name}")
        seen.add(name)
        if not _is_safe_manifest_arcname(name):
            problems.append(f"Unsafe zip member path: {name!r}")
        if info.is_dir():
            continue
        if not _zip_member_is_regular(info):
            problems.append(f"Zip member is not a regular file: {name}")
        if info.flag_bits & 0x1:
            problems.append(f"Encrypted zip member is not supported: {name}")
        if info.compress_type not in SUPPORTED_ZIP_COMPRESSION:
            problems.append(f"Unsupported zip compression method: {name}")
        if info.file_size > MAX_MEMBER_BYTES:
            problems.append(f"Zip member is too large: {name}")
        total += info.file_size
    if total > MAX_TOTAL_UNCOMPRESSED_BYTES:
        problems.append("Archive uncompressed payload exceeds safety limit")
    return problems


def verify_archive(
    filename: str,
    *,
    layout: InstanceLayout,
) -> dict:
    """Verify a save archive manifest and payload checksums without restoring."""
    with _open_existing_archive(
        filename,
        layout=layout,
    ) as (path, stat_info, archive):
        structure = _validate_zip_structure(archive)
        if structure:
            return _structure_failure_verification(path, stat_info, structure)
        manifest = _read_manifest_from_open_archive(archive)
        archive_summary_data = _archive_summary_from_manifest(
            path,
            stat_info,
            manifest,
        )
        return _verify_open_archive(archive, archive_summary_data, manifest)


def verify_archive_path(path: Path, *, expected_filename: str | None = None) -> dict:
    """Verify an already-openable path used by create/import before publication."""
    if path.is_symlink() or not path.is_file():
        raise ArchiveInvalidError("Archive must be a regular file")
    path_stat = path.stat()
    if path_stat.st_size > MAX_ARCHIVE_BYTES:
        raise ArchiveInvalidError("Archive exceeds compressed size limit")
    try:
        with path.open("rb") as handle, zipfile.ZipFile(handle, "r") as archive:
            structure = _validate_zip_structure(archive)
            if structure:
                return _structure_failure_verification(path, path_stat, structure)
            manifest = _read_manifest_from_open_archive(archive)
            summary = _archive_summary_from_manifest(
                path,
                path_stat,
                manifest,
            )
            if expected_filename:
                summary["filename"] = expected_filename
            return _verify_open_archive(archive, summary, manifest)
    except zipfile.BadZipFile as exc:
        raise ArchiveInvalidError("Archive zip cannot be read") from exc


def _verify_open_archive(
    archive: zipfile.ZipFile,
    archive_summary_data: dict,
    manifest: dict,
) -> dict:
    files = _manifest_files(manifest)
    problems: list[str] = _validate_zip_structure(archive)
    restorable_files: list[str] = []
    names = set(archive.namelist())
    declared = set()
    for record in files:
        arcname = record["path"]
        expected_digest = record["sha256"]
        if not _is_safe_manifest_arcname(arcname):
            problems.append(f"Unsafe manifest archive path: {arcname!r}")
            continue
        if arcname == MANIFEST_NAME:
            problems.append(f"Manifest must not declare itself as payload: {arcname}")
            continue
        declared.add(arcname)
        owner = DATA_CATALOG.find_for_logical_path(arcname)
        if owner is None:
            problems.append(f"Unsupported restore path: {arcname}")
            continue
        if arcname not in names:
            problems.append(f"Manifest payload is missing from zip: {arcname}")
            continue
        try:
            actual_digest = _sha256_zip_member(archive, arcname)
            info = archive.getinfo(arcname)
        except (KeyError, OSError, zipfile.BadZipFile) as exc:
            problems.append(f"Cannot read zip payload {arcname}: {exc}")
            continue
        if actual_digest != expected_digest:
            problems.append(f"Checksum mismatch for {arcname}")
            continue
        if info.file_size != record["size"]:
            problems.append(f"File record size mismatch for {arcname}")
            continue
        restorable_files.append(arcname)

    extras = sorted(
        name for name in names
        if name != MANIFEST_NAME and not name.endswith("/") and name not in declared
    )
    problems.extend(f"Zip contains undeclared payload file: {name}" for name in extras)

    return {
        "archive": archive_summary_data,
        "manifest": manifest,
        "verified": not problems,
        "problems": problems,
        "restorable_files": sorted(restorable_files),
    }


def _structure_failure_verification(
    path: Path,
    stat_info: object,
    problems: list[str],
) -> dict:
    fallback_created_at = _format_created_at(
        datetime.fromtimestamp(stat_info.st_mtime, timezone.utc)
    )
    return {
        "archive": {
            "filename": path.name,
            "size": stat_info.st_size,
            "created_at": fallback_created_at,
            "valid": False,
        },
        "manifest": None,
        "verified": False,
        "problems": problems,
        "restorable_files": [],
    }


def delete_archive(
    filename: str,
    *,
    layout: InstanceLayout,
) -> dict:
    """Delete a regular archive zip and return its summary."""
    path = _existing_regular_archive_path(filename, layout=layout)
    summary = archive_summary(path)
    try:
        path.unlink()
    except FileNotFoundError as exc:
        raise ArchiveNotFoundError(f"Archive not found: {filename}") from exc
    return summary


def list_archives(
    *,
    layout: InstanceLayout,
) -> list[dict]:
    """List local zip archive summaries, newest first."""
    target_dir = backups_dir(layout)
    if not target_dir.exists() or not target_dir.is_dir():
        return []
    archives = [
        archive_summary(path)
        for path in target_dir.iterdir()
        if not path.is_symlink() and path.is_file() and path.suffix.lower() == ".zip"
    ]
    return sorted(archives, key=lambda item: (item["created_at"], item["filename"]), reverse=True)


def import_archive(
    filename: str,
    source: BinaryIO,
    *,
    layout: InstanceLayout,
    max_bytes: int = MAX_ARCHIVE_BYTES,
) -> dict:
    """Stream an uploaded archive into local inventory and verify before publish."""
    safe_archive_path(filename, layout=layout)
    target_dir = backups_dir(layout)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = safe_archive_path(filename, layout=layout)
    if target.exists():
        target = target_dir / (
            f"{target.stem}-imported-{uuid4().hex[:8]}{target.suffix.lower()}"
        )
    tmp = target.with_name(f"{target.name}.importing")
    written = 0
    try:
        with tmp.open("xb") as handle:
            while True:
                chunk = source.read(1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > max_bytes:
                    raise ArchiveInvalidError("Imported archive exceeds size limit")
                handle.write(chunk)
            handle.flush()
        verification = verify_archive_path(tmp, expected_filename=target.name)
        if not verification["verified"]:
            raise ArchiveInvalidError(
                "Imported archive verification failed: "
                + "; ".join(verification["problems"])
            )
        tmp.replace(target)
        return {
            "archive": archive_summary(target),
            "verification": verification,
        }
    except Exception:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
        raise


def export_archive_path(
    filename: str,
    *,
    layout: InstanceLayout,
) -> Path:
    """Return a verified regular file path for the Dashboard download route."""
    path = _existing_regular_archive_path(
        filename,
        layout=layout,
    )
    verification = verify_archive(filename, layout=layout)
    if not verification["verified"]:
        raise ArchiveInvalidError("Archive cannot be exported because verification failed")
    return path
