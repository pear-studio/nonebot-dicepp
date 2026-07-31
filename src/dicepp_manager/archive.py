"""Manager-owned archive repository and low-level snapshot primitives."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import sys
import tempfile
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Iterator
from uuid import uuid4

from packaging.version import InvalidVersion, Version

from dicepp_data import (
    ARCHIVE_PROFILE_FULL,
    ARCHIVE_PROFILE_REGULAR,
    DATA_CATALOG,
    DataAssetKind,
    InstanceLayout,
)

from dicepp_meta import get_version as get_dicepp_version

ARCHIVE_FORMAT_VERSION = 3
LEGACY_ARCHIVE_FORMAT_VERSION = 1
STRUCTURED_ARCHIVE_FORMAT_VERSIONS = {2, ARCHIVE_FORMAT_VERSION}
MANIFEST_NAME = "manifest.json"
CHECKSUM_ALGORITHM = "sha256"
SUPPORTED_PROFILES = {ARCHIVE_PROFILE_REGULAR, ARCHIVE_PROFILE_FULL}
MAX_ARCHIVE_BYTES = 16 * 1024**3
MAX_MEMBER_BYTES = 8 * 1024**3
MAX_TOTAL_UNCOMPRESSED_BYTES = 32 * 1024**3
MAX_MEMBER_COUNT = 100_000
MAX_COMPRESSION_RATIO = 250
MAX_MANIFEST_BYTES = 2 * 1024**2
SUPPORTED_ZIP_COMPRESSION = {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
LEGACY_V1_ASSET_IDS = {
    "config.user",
    "config.bots",
    "data.instance",
    "data.bot_core",
    "data.bot_log",
    "data.persona",
    "data.local_images",
}

SCOPE_INCLUDED = [
    asset.logical_glob
    for asset in DATA_CATALOG.for_profile(ARCHIVE_PROFILE_REGULAR)
]
SCOPE_EXCLUDED = [
    "config/global.json",
    "config/bots/_template.json",
    "dashboard/data/dashboard.db",
    "content",
    "data/backups",
    "data/runtime",
    "data/bots/*/logs",
    "protocol adapter data",
    "LLOneBot data",
]


@dataclass(frozen=True)
class ArchivePayload:
    """A local filesystem file and its POSIX archive name."""

    path: Path
    arcname: str


@dataclass(frozen=True)
class SQLiteSchemaInspection:
    """Result of checking one catalogued SQLite payload."""

    opaque: bool = False
    problem: str | None = None


class ArchiveError(Exception):
    """Raised when an archive cannot be created safely."""


class ArchiveNameError(ArchiveError):
    """Raised when a requested archive filename is not safe."""


class ArchiveNotFoundError(ArchiveError):
    """Raised when a requested archive does not exist as a regular zip file."""


class ArchiveInvalidError(ArchiveError):
    """Raised when an archive zip cannot provide a manifest."""


class ArchiveRestorePlanVerificationError(ArchiveError):
    """Raised when a restore plan is requested for an unverified archive."""

    def __init__(self, verification: dict):
        super().__init__("Archive verification failed")
        self.verification = verification


class ArchiveRestorePlanBlockedError(ArchiveError):
    """Raised when a restore plan contains entries that cannot be restored."""

    def __init__(self, plan: dict):
        super().__init__("Archive restore plan is blocked")
        self.plan = plan


def backups_dir(layout: InstanceLayout) -> Path:
    """Return the Manager-owned archive directory."""
    return layout.manager_backups_dir


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


def collect_archive_payloads(
    layout: InstanceLayout,
    profile: str = ARCHIVE_PROFILE_REGULAR,
) -> list[ArchivePayload]:
    """Collect files included in one archive profile."""
    return [
        ArchivePayload(path=match.path, arcname=_safe_arcname(match.logical_path))
        for match in DATA_CATALOG.collect(layout, profile)
    ]


def _checkpoint_managed_sqlite_assets(layout: InstanceLayout, profile: str) -> None:
    """Fold every catalogued SQLite WAL into its main database before snapshotting.

    Archive creation only stores the main ``.db`` payload.  After the Runtime has
    stopped, a successful truncate checkpoint makes that payload a complete
    snapshot while keeping the archive format independent from SQLite sidecars.
    """
    for match in DATA_CATALOG.collect(layout, profile):
        asset = DATA_CATALOG.find_for_logical_path(match.logical_path, profile=profile)
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


def _validate_profile(profile: str) -> str:
    if profile not in SUPPORTED_PROFILES:
        raise ArchiveError(f"Unsupported archive profile: {profile!r}")
    return profile


def estimate_archive(
    layout: InstanceLayout,
    profile: str = ARCHIVE_PROFILE_REGULAR,
) -> dict:
    """Estimate snapshot input and available Manager repository space."""
    profile = _validate_profile(profile)
    payloads = collect_archive_payloads(layout, profile)
    total = 0
    for payload in payloads:
        try:
            total += payload.path.stat().st_size
        except OSError:
            continue
    target = backups_dir(layout)
    probe = target if target.exists() else target.parent
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    free = shutil.disk_usage(probe).free
    return {
        "profile": profile,
        "file_count": len(payloads),
        "input_bytes": total,
        "available_bytes": free,
        "enough_space": free > total + 64 * 1024**2,
        "requires_runtime_stop": True,
        "large_content_warning": (
            profile == ARCHIVE_PROFILE_FULL and total >= 256 * 1024**2
        ),
    }


def _archive_filename(now: datetime, description: str | None) -> str:
    slug = sanitize_description_slug(description)
    suffix = f"-{slug}" if slug else ""
    return f"{_filename_timestamp(now)}{suffix}-{uuid4().hex[:8]}.zip"


def _build_manifest(
    *,
    created_at: str,
    description: str,
    profile: str,
    archive_kind: str,
    files: list[dict],
    opaque_sqlite_files: list[str],
) -> dict:
    grouped: dict[str, list[dict]] = {}
    checksums: dict[str, str] = {}
    for item in files:
        grouped.setdefault(str(item["asset_id"]), []).append(
            {
                "path": item["path"],
                "size": item["size"],
                "sha256": item["sha256"],
            }
        )
        checksums[str(item["path"])] = str(item["sha256"])
    assets = []
    for asset in DATA_CATALOG.for_profile(profile):
        assets.append(
            {
                "id": asset.id,
                "kind": asset.kind.value,
                "schema": asset.schema.to_dict() if asset.schema else None,
                "sensitive": asset.sensitive,
                "files": sorted(grouped.get(asset.id, []), key=lambda row: row["path"]),
            }
        )
    return {
        "format_version": ARCHIVE_FORMAT_VERSION,
        "created_at": created_at,
        "dicepp_version": get_dicepp_version(),
        "source_platform": sys.platform,
        "description": description,
        "profile": profile,
        "archive_kind": archive_kind,
        "sensitive": any(asset["sensitive"] for asset in assets),
        "compatibility": {
            "opaque_sqlite": {
                "count": len(opaque_sqlite_files),
                "files": sorted(opaque_sqlite_files),
            },
        },
        "catalog": {
            "digest": DATA_CATALOG.digest,
            "description": DATA_CATALOG.to_dict(),
        },
        "assets": assets,
        "files": sorted(files, key=lambda row: row["path"]),
        "scope": {
            "included": [
                asset.logical_glob for asset in DATA_CATALOG.for_profile(profile)
            ],
            "excluded": SCOPE_EXCLUDED,
        },
        "checksum": {
            "algorithm": CHECKSUM_ALGORITHM,
            "files": checksums,
        },
    }


def _path_matches_open_file(path_stat: os.stat_result, file_stat: os.stat_result) -> bool:
    try:
        return os.path.samestat(path_stat, file_stat)
    except OSError:
        return (
            path_stat.st_ino == file_stat.st_ino
            and path_stat.st_dev == file_stat.st_dev
        )


def _open_regular_payload(path: Path):
    """Open *path* only if it still names the same non-symlink regular file."""
    if path.is_symlink():
        return None
    try:
        handle = path.open("rb")
    except (FileNotFoundError, OSError):
        return None

    try:
        file_stat = os.fstat(handle.fileno())
        path_stat = os.lstat(path)
    except OSError:
        handle.close()
        return None

    if (
        not stat.S_ISREG(file_stat.st_mode)
        or stat.S_ISLNK(path_stat.st_mode)
        or not _path_matches_open_file(path_stat, file_stat)
    ):
        handle.close()
        return None
    return handle


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
    archive_dir: Path | None = None,
    profile: str = ARCHIVE_PROFILE_REGULAR,
    archive_kind: str = "manual",
    phase_callback=None,
) -> tuple[dict, dict]:
    """Create a local zip archive and return ``(summary, manifest)``."""
    profile = _validate_profile(profile)
    if archive_kind not in {"manual", "system"}:
        raise ArchiveError(f"Unsupported archive kind: {archive_kind!r}")
    target_dir = archive_dir or backups_dir(layout)
    target_dir.mkdir(parents=True, exist_ok=True)
    now = _utc_now()
    filename = _archive_filename(now, description)
    target = target_dir / filename
    tmp = target.with_name(f"{target.name}.inprogress")
    _checkpoint_managed_sqlite_assets(layout, profile)
    payloads = collect_archive_payloads(layout, profile)
    opaque_sqlite_files = _inspect_source_sqlite_payloads(payloads, profile=profile)
    file_records: list[dict] = []
    manifest: dict | None = None

    try:
        with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            if phase_callback is not None:
                phase_callback("stream")
            for payload in payloads:
                written = _write_payload_to_archive(archive, payload)
                if written is None:
                    raise ArchiveError(
                        f"Archive payload cannot be read safely: {payload.arcname}"
                    )
                checksum, size = written
                asset = DATA_CATALOG.find_for_logical_path(
                    payload.arcname,
                    profile=profile,
                )
                if asset is None:
                    raise ArchiveError(
                        f"Collected payload is not owned by the profile: {payload.arcname}"
                    )
                file_records.append(
                    {
                        "path": payload.arcname,
                        "asset_id": asset.id,
                        "size": size,
                        "sha256": checksum,
                    }
                )
            manifest = _build_manifest(
                created_at=_format_created_at(now),
                description=description or "",
                profile=profile,
                archive_kind=archive_kind,
                files=file_records,
                opaque_sqlite_files=opaque_sqlite_files,
            )
            archive.writestr(
                MANIFEST_NAME,
                json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
            )
        _fsync_file(tmp)
        # Verify the exact bytes written before making the archive observable.
        verification = verify_archive_path(tmp, expected_filename=target.name)
        if not verification["verified"]:
            raise ArchiveInvalidError(
                "New archive verification failed: " + "; ".join(verification["problems"])
            )
        if phase_callback is not None:
            phase_callback("publish")
        os.replace(tmp, target)
        _fsync_directory(target_dir)
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
    archive_dir: Path | None = None,
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

    target_dir = archive_dir or backups_dir(layout)
    return target_dir / filename


def _existing_regular_archive_path(
    filename: str,
    *,
    layout: InstanceLayout,
    archive_dir: Path | None = None,
) -> Path:
    path = safe_archive_path(filename, layout=layout, archive_dir=archive_dir)
    try:
        path_stat = os.lstat(path)
    except FileNotFoundError as exc:
        raise ArchiveNotFoundError(f"Archive not found: {filename}") from exc
    except OSError as exc:
        raise ArchiveNotFoundError(f"Archive not found: {filename}") from exc

    if not stat.S_ISREG(path_stat.st_mode) or stat.S_ISLNK(path_stat.st_mode):
        raise ArchiveNotFoundError(f"Archive not found: {filename}")
    return path


def _same_file_identity(left: os.stat_result, right: os.stat_result) -> bool:
    return (
        left.st_dev == right.st_dev
        and left.st_ino == right.st_ino
        and left.st_size == right.st_size
        and left.st_mtime == right.st_mtime
    )


@contextmanager
def _open_existing_archive(
    filename: str,
    *,
    layout: InstanceLayout,
    archive_dir: Path | None = None,
) -> Iterator[tuple[Path, os.stat_result, zipfile.ZipFile]]:
    """Open one regular archive file and keep the fd through the operation."""
    path = safe_archive_path(filename, layout=layout, archive_dir=archive_dir)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = -1
    handle: BinaryIO | None = None
    archive: zipfile.ZipFile | None = None
    try:
        try:
            fd = os.open(path, flags)
        except FileNotFoundError as exc:
            raise ArchiveNotFoundError(f"Archive not found: {filename}") from exc
        except OSError as exc:
            raise ArchiveNotFoundError(f"Archive not found: {filename}") from exc

        fd_stat = os.fstat(fd)
        if not stat.S_ISREG(fd_stat.st_mode):
            raise ArchiveNotFoundError(f"Archive not found: {filename}")

        try:
            path_stat = os.lstat(path)
        except FileNotFoundError as exc:
            raise ArchiveNotFoundError(f"Archive not found: {filename}") from exc
        except OSError as exc:
            raise ArchiveNotFoundError(f"Archive not found: {filename}") from exc
        if stat.S_ISLNK(path_stat.st_mode) or not stat.S_ISREG(path_stat.st_mode):
            raise ArchiveNotFoundError(f"Archive not found: {filename}")
        if not _same_file_identity(fd_stat, path_stat):
            raise ArchiveInvalidError(f"Archive changed while opening: {filename}")

        handle = os.fdopen(fd, "rb")
        fd = -1
        try:
            archive = zipfile.ZipFile(handle, "r")
        except zipfile.BadZipFile as exc:
            raise ArchiveInvalidError("Archive zip cannot be read") from exc
        yield path, fd_stat, archive
    finally:
        if archive is not None:
            archive.close()
        if handle is not None:
            handle.close()
        if fd != -1:
            os.close(fd)


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
            profile = _manifest_profile(manifest)
            opaque_sqlite_count = _manifest_opaque_sqlite_count(manifest)
    except (OSError, ArchiveError, KeyError, json.JSONDecodeError, zipfile.BadZipFile):
        return summary

    if isinstance(manifest, dict):
        summary["valid"] = True
        summary["created_at"] = manifest.get("created_at") or fallback_created_at
        summary["description"] = manifest.get("description", "")
        summary["format_version"] = manifest.get("format_version")
        summary["dicepp_version"] = manifest.get("dicepp_version", "")
        summary["profile"] = profile
        summary["archive_kind"] = manifest.get("archive_kind", "manual")
        summary["sensitive"] = bool(manifest.get("sensitive"))
        summary["opaque_sqlite_count"] = opaque_sqlite_count
        checksum = manifest.get("checksum")
        files = checksum.get("files") if isinstance(checksum, dict) else None
        summary["file_count"] = len(files) if isinstance(files, dict) else 0
    return summary


def _archive_summary_from_manifest(
    path: Path,
    stat_info: os.stat_result,
    manifest: dict,
) -> dict:
    fallback_created_at = _format_created_at(
        datetime.fromtimestamp(stat_info.st_mtime, timezone.utc)
    )
    checksum = manifest.get("checksum")
    files = checksum.get("files") if isinstance(checksum, dict) else None
    return {
        "filename": path.name,
        "size": stat_info.st_size,
        "created_at": manifest.get("created_at") or fallback_created_at,
        "valid": True,
        "description": manifest.get("description", ""),
        "format_version": manifest.get("format_version"),
        "dicepp_version": manifest.get("dicepp_version", ""),
        "profile": _manifest_profile(manifest),
        "archive_kind": manifest.get("archive_kind", "manual"),
        "sensitive": bool(manifest.get("sensitive")),
        "opaque_sqlite_count": _manifest_opaque_sqlite_count(manifest),
        "file_count": len(files) if isinstance(files, dict) else 0,
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
    archive_dir: Path | None = None,
) -> tuple[dict, dict]:
    """Return ``(summary, manifest)`` for a regular archive zip filename."""
    with _open_existing_archive(
        filename,
        layout=layout,
        archive_dir=archive_dir,
    ) as (path, stat_info, archive):
        structure = _validate_zip_structure(archive)
        if structure:
            raise ArchiveInvalidError("; ".join(structure))
        manifest = _read_manifest_from_open_archive(archive)
        return _archive_summary_from_manifest(path, stat_info, manifest), manifest


def _validate_manifest_for_verify(manifest: dict) -> dict[str, str]:
    format_version = manifest.get("format_version")
    if format_version not in {
        LEGACY_ARCHIVE_FORMAT_VERSION,
        *STRUCTURED_ARCHIVE_FORMAT_VERSIONS,
    }:
        raise ArchiveInvalidError("Unsupported archive format version")
    if format_version in STRUCTURED_ARCHIVE_FORMAT_VERSIONS:
        profile = manifest.get("profile")
        _validate_profile(profile)
        catalog = manifest.get("catalog")
        if not isinstance(catalog, dict) or not isinstance(catalog.get("digest"), str):
            raise ArchiveInvalidError("Archive manifest catalog is missing")
        files_v2 = manifest.get("files")
        if not isinstance(files_v2, list):
            raise ArchiveInvalidError("Archive manifest files must be an array")
        opaque_sqlite_files = _manifest_opaque_sqlite_files(manifest)
        if (
            format_version == ARCHIVE_FORMAT_VERSION
            and opaque_sqlite_files is None
        ):
            raise ArchiveInvalidError(
                "Archive manifest opaque SQLite declaration is missing"
            )
    checksum = manifest.get("checksum")
    if not isinstance(checksum, dict):
        raise ArchiveInvalidError("Archive manifest checksum must be an object")
    if checksum.get("algorithm") != CHECKSUM_ALGORITHM:
        raise ArchiveInvalidError("Unsupported archive checksum algorithm")
    files = checksum.get("files")
    if not isinstance(files, dict):
        raise ArchiveInvalidError("Archive manifest checksum.files must be an object")
    return files


def _manifest_opaque_sqlite_files(manifest: dict) -> list[str] | None:
    compatibility = manifest.get("compatibility")
    if compatibility is None:
        return None
    if not isinstance(compatibility, dict):
        raise ArchiveInvalidError("Archive manifest compatibility must be an object")
    opaque = compatibility.get("opaque_sqlite")
    if opaque is None:
        return None
    if not isinstance(opaque, dict):
        raise ArchiveInvalidError(
            "Archive manifest compatibility.opaque_sqlite must be an object"
        )
    count = opaque.get("count")
    files = opaque.get("files")
    if (
        not isinstance(count, int)
        or count < 0
        or not isinstance(files, list)
        or any(
            not isinstance(path, str) or not _is_safe_manifest_arcname(path)
            for path in files
        )
        or len(set(files)) != len(files)
        or count != len(files)
    ):
        raise ArchiveInvalidError("Archive manifest opaque SQLite declaration is invalid")
    return sorted(files)


def _manifest_opaque_sqlite_count(manifest: dict) -> int | None:
    files = _manifest_opaque_sqlite_files(manifest)
    if (
        manifest.get("format_version") == ARCHIVE_FORMAT_VERSION
        and files is None
    ):
        raise ArchiveInvalidError(
            "Archive manifest opaque SQLite declaration is missing"
        )
    return len(files) if files is not None else None


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


def _archive_arcname_in_restore_scope(arcname: str) -> bool:
    # Legacy v1 archives are always interpreted as regular.
    return DATA_CATALOG.find_for_logical_path(
        arcname,
        profile=ARCHIVE_PROFILE_REGULAR,
    ) is not None


def _manifest_profile(manifest: dict) -> str:
    if manifest.get("format_version") == LEGACY_ARCHIVE_FORMAT_VERSION:
        return ARCHIVE_PROFILE_REGULAR
    try:
        return _validate_profile(str(manifest.get("profile", "")))
    except ArchiveError as exc:
        raise ArchiveInvalidError(str(exc)) from exc


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
        if info.compress_size == 0 and info.file_size:
            problems.append(f"Suspicious zero-size compressed member: {name}")
        elif (
            info.compress_size
            and info.file_size / info.compress_size > MAX_COMPRESSION_RATIO
            and info.file_size > 1024**2
        ):
            problems.append(f"Suspicious compression ratio: {name}")
    if total > MAX_TOTAL_UNCOMPRESSED_BYTES:
        problems.append("Archive uncompressed payload exceeds safety limit")
    return problems


def verify_archive(
    filename: str,
    *,
    layout: InstanceLayout,
    archive_dir: Path | None = None,
) -> dict:
    """Verify a save archive manifest and payload checksums without restoring."""
    with _open_existing_archive(
        filename,
        layout=layout,
        archive_dir=archive_dir,
    ) as (path, stat_info, archive):
        structure = _validate_zip_structure(archive)
        if structure:
            return _structure_failure_verification(path, stat_info, structure)
        manifest = _read_manifest_from_open_archive(archive)
        archive_summary_data = _archive_summary_from_manifest(path, stat_info, manifest)
        return _verify_open_archive(archive, archive_summary_data, manifest)


def verify_archive_path(path: Path, *, expected_filename: str | None = None) -> dict:
    """Verify an already-openable path used by create/import before publication."""
    try:
        path_stat = os.lstat(path)
    except OSError as exc:
        raise ArchiveInvalidError("Archive cannot be inspected") from exc
    if not stat.S_ISREG(path_stat.st_mode) or stat.S_ISLNK(path_stat.st_mode):
        raise ArchiveInvalidError("Archive must be a regular file")
    if path_stat.st_size > MAX_ARCHIVE_BYTES:
        raise ArchiveInvalidError("Archive exceeds compressed size limit")
    try:
        with path.open("rb") as handle, zipfile.ZipFile(handle, "r") as archive:
            structure = _validate_zip_structure(archive)
            if structure:
                return _structure_failure_verification(path, path_stat, structure)
            manifest = _read_manifest_from_open_archive(archive)
            summary = _archive_summary_from_manifest(path, path_stat, manifest)
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
    checksum_files = _validate_manifest_for_verify(manifest)

    problems: list[str] = _validate_zip_structure(archive)
    warnings: list[str] = []
    restorable_files: list[str] = []
    opaque_sqlite_files: list[str] = []
    profile = _manifest_profile(manifest)
    manifest_asset_map: dict[str, dict] = {}

    if manifest.get("format_version") in STRUCTURED_ARCHIVE_FORMAT_VERSIONS:
        archive_version = manifest.get("dicepp_version")
        current_version = get_dicepp_version()
        parsed_archive_version: Version | None = None
        try:
            parsed_archive_version = Version(archive_version)
        except (InvalidVersion, TypeError):
            problems.append("Archive DicePP version is missing or invalid")
        try:
            parsed_current_version = Version(current_version)
        except (InvalidVersion, TypeError):
            problems.append("Current DicePP version cannot be determined")
        else:
            if (
                parsed_archive_version is not None
                and parsed_archive_version > parsed_current_version
            ):
                problems.append(
                    "Archive was created by a newer DicePP version: "
                    f"{archive_version} > {current_version}"
                )
        catalog = manifest["catalog"]
        if catalog.get("digest") != DATA_CATALOG.digest:
            warnings.append(
                "DataAsset catalog digest differs; compatibility was checked per asset"
            )
        records = manifest.get("files", [])
        record_map = {
            item.get("path"): item
            for item in records
            if isinstance(item, dict) and isinstance(item.get("path"), str)
        }
        if len(record_map) != len(records):
            problems.append("Archive manifest contains duplicate or invalid file records")
        for path, digest in checksum_files.items():
            record = record_map.get(path)
            if not isinstance(record, dict):
                problems.append(f"Missing v2 file record for {path}")
            elif record.get("sha256") != digest:
                problems.append(f"File record checksum mismatch for {path}")
            elif not isinstance(record.get("size"), int) or record["size"] < 0:
                problems.append(f"Invalid file record size for {path}")
        manifest_assets = manifest.get("assets")
        if not isinstance(manifest_assets, list):
            problems.append("Archive manifest assets must be an array")
            manifest_assets = []
        expected_assets = {
            asset.id: asset for asset in DATA_CATALOG.for_profile(profile)
        }
        seen_asset_ids: set[str] = set()
        asset_declared_paths: set[tuple[str, str]] = set()
        for item in manifest_assets:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                problems.append("Archive manifest contains an invalid asset record")
                continue
            asset_id = item["id"]
            if asset_id in seen_asset_ids:
                problems.append(f"Duplicate archive asset record: {asset_id}")
                continue
            seen_asset_ids.add(asset_id)
            manifest_asset_map[asset_id] = item
            expected = expected_assets.get(asset_id)
            if expected is None:
                problems.append(f"Archive contains unsupported asset: {asset_id}")
                continue
            schema = item.get("schema")
            if expected.schema is None:
                if schema is not None:
                    problems.append(f"Unexpected schema reference for {asset_id}")
            elif not isinstance(schema, dict):
                problems.append(f"Missing schema reference for {asset_id}")
            elif schema.get("name") != expected.schema.name:
                problems.append(f"Schema identity mismatch for {asset_id}")
            elif not isinstance(schema.get("latest_version"), int):
                problems.append(f"Invalid schema version for {asset_id}")
            elif schema["latest_version"] > expected.schema.latest_version:
                problems.append(
                    f"Archive schema is newer than this DicePP version: "
                    f"{asset_id}@{schema['latest_version']}"
                )
            asset_files = item.get("files")
            if not isinstance(asset_files, list):
                problems.append(f"Asset files must be an array: {asset_id}")
                continue
            for asset_file in asset_files:
                if not isinstance(asset_file, dict):
                    problems.append(f"Invalid asset file record: {asset_id}")
                    continue
                path = asset_file.get("path")
                top_record = record_map.get(path)
                if (
                    not isinstance(path, str)
                    or not isinstance(top_record, dict)
                    or top_record.get("asset_id") != asset_id
                    or asset_file.get("size") != top_record.get("size")
                    or asset_file.get("sha256") != top_record.get("sha256")
                ):
                    problems.append(f"Asset file record mismatch: {asset_id}:{path}")
                else:
                    asset_declared_paths.add((asset_id, path))
        missing_assets = set(expected_assets) - seen_asset_ids
        if missing_assets:
            warnings.append(
                "Current DicePP has additional assets that will be preserved: "
                + ", ".join(sorted(missing_assets))
            )
        for path, record in record_map.items():
            if not isinstance(record, dict):
                continue
            owner = DATA_CATALOG.find_for_logical_path(path, profile=profile)
            if owner is not None and record.get("asset_id") != owner.id:
                problems.append(f"File record asset mismatch for {path}")
            asset_id = record.get("asset_id") if isinstance(record, dict) else None
            if (
                isinstance(asset_id, str)
                and (asset_id, path) not in asset_declared_paths
            ):
                problems.append(f"File record is absent from asset declaration: {path}")

    try:
        names = set(archive.namelist())
        for arcname, expected_digest in checksum_files.items():
            if not isinstance(arcname, str) or not _is_safe_manifest_arcname(arcname):
                problems.append(f"Unsafe manifest archive path: {arcname!r}")
                continue
            if arcname == MANIFEST_NAME:
                problems.append(f"Manifest must not declare itself as payload: {arcname}")
                continue
            if not isinstance(expected_digest, str):
                problems.append(f"Invalid checksum digest for {arcname}")
                continue
            if arcname not in names:
                problems.append(f"Manifest payload is missing from zip: {arcname}")
                continue
            try:
                actual_digest = _sha256_zip_member(archive, arcname)
            except (KeyError, OSError, zipfile.BadZipFile) as exc:
                problems.append(f"Cannot read zip payload {arcname}: {exc}")
                continue
            if actual_digest != expected_digest:
                problems.append(f"Checksum mismatch for {arcname}")
                continue
            if DATA_CATALOG.find_for_logical_path(arcname, profile=profile) is None:
                problems.append(f"Unsupported restore path: {arcname}")
                continue
            owner = DATA_CATALOG.find_for_logical_path(arcname, profile=profile)
            if owner is not None and owner.schema is not None:
                declared_schema = None
                if (
                    manifest.get("format_version")
                    in STRUCTURED_ARCHIVE_FORMAT_VERSIONS
                ):
                    record = record_map.get(arcname)
                    asset_id = record.get("asset_id") if isinstance(record, dict) else None
                    asset_record = (
                        manifest_asset_map.get(asset_id)
                        if isinstance(asset_id, str)
                        else None
                    )
                    declared_schema = (
                        asset_record.get("schema")
                        if isinstance(asset_record, dict)
                        else None
                    )
                schema_inspection = _inspect_archived_sqlite_schema(
                    archive,
                    arcname,
                    expected_name=owner.schema.name,
                    maximum_version=owner.schema.latest_version,
                    declared_schema=declared_schema,
                )
                if schema_inspection.problem is not None:
                    problems.append(schema_inspection.problem)
                    continue
                if schema_inspection.opaque:
                    opaque_sqlite_files.append(arcname)
            if (
                manifest.get("format_version")
                in STRUCTURED_ARCHIVE_FORMAT_VERSIONS
            ):
                record = record_map.get(arcname)
                try:
                    info = archive.getinfo(arcname)
                except KeyError:
                    info = None
                if info is not None and isinstance(record, dict) and info.file_size != record.get("size"):
                    problems.append(f"File record size mismatch for {arcname}")
                    continue
            restorable_files.append(arcname)

        declared = {
            arcname
            for arcname in checksum_files
            if isinstance(arcname, str)
        }
        extras = sorted(
            name for name in names
            if name != MANIFEST_NAME and not name.endswith("/") and name not in declared
        )
        for arcname in extras:
            problems.append(f"Zip contains undeclared payload file: {arcname}")
    except zipfile.BadZipFile as exc:
        raise ArchiveInvalidError("Archive zip cannot be read") from exc

    opaque_sqlite_files.sort()
    declared_opaque_files = (
        _manifest_opaque_sqlite_files(manifest)
        if manifest.get("format_version") in STRUCTURED_ARCHIVE_FORMAT_VERSIONS
        else None
    )
    if (
        declared_opaque_files is not None
        and declared_opaque_files != opaque_sqlite_files
    ):
        problems.append(
            "Archive manifest opaque SQLite declaration does not match payloads"
        )
    if opaque_sqlite_files:
        warnings.append(
            f"{len(opaque_sqlite_files)} SQLite database(s) without "
            "schema_metadata were preserved as opaque files"
        )
    archive_summary_data["opaque_sqlite_count"] = len(opaque_sqlite_files)

    return {
        "archive": archive_summary_data,
        "manifest": manifest,
        "verified": not problems,
        "problems": problems,
        "warnings": warnings,
        "opaque_sqlite": {
            "count": len(opaque_sqlite_files),
            "files": opaque_sqlite_files,
        },
        "restorable_files": sorted(restorable_files),
        "profile": profile,
        "sensitive": bool(manifest.get("sensitive")),
        "declared_asset_ids": (
            sorted(seen_asset_ids)
            if manifest.get("format_version") in STRUCTURED_ARCHIVE_FORMAT_VERSIONS
            else sorted(_legacy_declared_asset_ids(manifest))
        ),
    }


def _legacy_declared_asset_ids(manifest: dict) -> set[str]:
    scope = manifest.get("scope")
    included = scope.get("included") if isinstance(scope, dict) else None
    if not isinstance(included, list):
        return set()
    patterns = {value for value in included if isinstance(value, str)}
    declared: set[str] = set()
    for asset in DATA_CATALOG.for_profile(ARCHIVE_PROFILE_REGULAR):
        if asset.id not in LEGACY_V1_ASSET_IDS:
            continue
        if asset.logical_glob in patterns or any(
            "*" not in pattern
            and "?" not in pattern
            and (
                asset.logical_glob == pattern
                or asset.logical_glob.startswith(f"{pattern.rstrip('/')}/")
            )
            for pattern in patterns
        ):
            declared.add(asset.id)
    return declared


def _structure_failure_verification(
    path: Path,
    stat_info: os.stat_result,
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
        "warnings": [],
        "opaque_sqlite": {"count": 0, "files": []},
        "restorable_files": [],
        "profile": ARCHIVE_PROFILE_REGULAR,
        "sensitive": False,
        "declared_asset_ids": [],
    }


def _inspect_archived_sqlite_schema(
    archive: zipfile.ZipFile,
    arcname: str,
    *,
    expected_name: str,
    maximum_version: int,
    declared_schema: dict | None,
) -> SQLiteSchemaInspection:
    """Cross-check archived SQLite metadata without extracting into the instance."""
    temporary_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix="dicepp-archive-schema-",
            suffix=".db",
            delete=False,
        ) as temporary:
            temporary_path = temporary.name
            with archive.open(arcname, "r") as source:
                remaining = MAX_MEMBER_BYTES
                while True:
                    chunk = source.read(min(1024 * 1024, remaining + 1))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    if remaining < 0:
                        return SQLiteSchemaInspection(
                            problem=f"SQLite payload exceeds limit: {arcname}"
                        )
                    temporary.write(chunk)
            temporary.flush()
            os.fsync(temporary.fileno())
        return _inspect_sqlite_schema_path(
            Path(temporary_path),
            display_path=arcname,
            expected_name=expected_name,
            maximum_version=maximum_version,
            declared_schema=declared_schema,
        )
    finally:
        if temporary_path is not None:
            try:
                Path(temporary_path).unlink()
            except OSError:
                pass


def _inspect_source_sqlite_payloads(
    payloads: list[ArchivePayload],
    *,
    profile: str,
) -> list[str]:
    opaque_files: list[str] = []
    for payload in payloads:
        asset = DATA_CATALOG.find_for_logical_path(payload.arcname, profile=profile)
        if asset is None or asset.schema is None:
            continue
        inspection = _inspect_sqlite_schema_path(
            payload.path,
            display_path=payload.arcname,
            expected_name=asset.schema.name,
            maximum_version=asset.schema.latest_version,
            declared_schema=asset.schema.to_dict(),
            check_integrity=False,
        )
        if inspection.problem is not None:
            raise ArchiveError(inspection.problem)
        if inspection.opaque:
            opaque_files.append(payload.arcname)
    return sorted(opaque_files)


def _inspect_sqlite_schema_path(
    path: Path,
    *,
    display_path: str,
    expected_name: str,
    maximum_version: int,
    declared_schema: dict | None,
    check_integrity: bool = True,
) -> SQLiteSchemaInspection:
    try:
        connection = sqlite3.connect(
            f"{path.resolve().as_uri()}?mode=ro",
            uri=True,
        )
        try:
            try:
                rows = connection.execute(
                    "SELECT key, value FROM schema_metadata"
                ).fetchall()
            except sqlite3.OperationalError as exc:
                if str(exc) == "no such table: schema_metadata":
                    if check_integrity:
                        integrity_rows = connection.execute(
                            "PRAGMA quick_check"
                        ).fetchall()
                        if integrity_rows != [("ok",)]:
                            return SQLiteSchemaInspection(
                                problem=(
                                    "SQLite integrity check failed: "
                                    f"{display_path}"
                                )
                            )
                    return SQLiteSchemaInspection(opaque=True)
                raise
        finally:
            connection.close()
    except sqlite3.Error as exc:
        return SQLiteSchemaInspection(
            problem=(
                f"SQLite schema metadata cannot be read: {display_path}: {exc}"
            )
        )
    metadata = {str(key): str(value) for key, value in rows}
    required_metadata = {
        "application",
        "target_name",
        "current_version",
        "created_at",
        "updated_at",
    }
    missing_metadata = sorted(required_metadata - set(metadata))
    if missing_metadata:
        return SQLiteSchemaInspection(
            problem=(
                f"SQLite schema metadata is incomplete: {display_path}; "
                f"missing {', '.join(missing_metadata)}"
            )
        )
    if metadata["application"] != "dicepp":
        return SQLiteSchemaInspection(
            problem=(
                f"SQLite schema application mismatch: {display_path}; "
                "expected dicepp"
            )
        )
    if metadata.get("target_name") != expected_name:
        return SQLiteSchemaInspection(
            problem=(
                f"SQLite schema identity mismatch: {display_path}; "
                f"expected {expected_name}"
            )
        )
    if declared_schema is not None and declared_schema.get("name") != metadata.get(
        "target_name"
    ):
        return SQLiteSchemaInspection(
            problem=(
                f"SQLite schema does not match manifest declaration: {display_path}"
            )
        )
    try:
        current_version = int(metadata["current_version"])
    except ValueError:
        return SQLiteSchemaInspection(
            problem=f"SQLite schema version is invalid: {display_path}"
        )
    if current_version < 1:
        return SQLiteSchemaInspection(
            problem=f"SQLite schema version is invalid: {display_path}"
        )
    if current_version > maximum_version:
        return SQLiteSchemaInspection(
            problem=(
                "Archive schema is newer than this DicePP version: "
                f"{display_path}@{current_version}"
            )
        )
    if declared_schema is not None:
        declared_version = declared_schema.get("latest_version")
        if not isinstance(declared_version, int) or current_version > declared_version:
            return SQLiteSchemaInspection(
                problem=(
                    "SQLite schema is newer than manifest declaration: "
                    f"{display_path}"
                )
            )
    return SQLiteSchemaInspection()


def _data_root(layout: InstanceLayout) -> Path:
    return layout.data_root


def _path_is_relative_to(path: Path, root: Path) -> bool:
    try:
        return path == root or path.is_relative_to(root)
    except ValueError:
        return False


def _restore_parent_precondition_problem(target: Path, root: Path) -> str | None:
    """Return a restore precondition problem without following symlink parents."""
    root_abs = _absolute_path(root)
    parent_abs = _absolute_path(target.parent)
    if not _path_is_relative_to(parent_abs, root_abs):
        return f"Restore target escapes allowed root: {target.name}"

    if root_abs.exists():
        try:
            root_stat = os.lstat(root_abs)
        except OSError as exc:
            return f"Restore root cannot be inspected: {root.name}: {exc}"
        if stat.S_ISLNK(root_stat.st_mode):
            return f"Restore root is a symlink: {root.name}"
        if not stat.S_ISDIR(root_stat.st_mode):
            return f"Restore root is not a directory: {root.name}"
    else:
        try:
            root_parent_stat = os.lstat(root_abs.parent)
        except OSError as exc:
            return f"Restore root parent cannot be inspected: {root_abs.parent.name}: {exc}"
        if stat.S_ISLNK(root_parent_stat.st_mode):
            return f"Restore root parent is a symlink: {root_abs.parent.name}"
        if not stat.S_ISDIR(root_parent_stat.st_mode):
            return f"Restore root parent is not a directory: {root_abs.parent.name}"

    chain: list[Path] = []
    current = parent_abs
    while True:
        chain.append(current)
        if current == root_abs:
            break
        if current == current.parent:
            return f"Restore target escapes allowed root: {target.name}"
        current = current.parent

    for candidate in reversed(chain):
        try:
            candidate_stat = os.lstat(candidate)
        except FileNotFoundError:
            continue
        except OSError as exc:
            return f"Restore parent cannot be inspected: {candidate.name}: {exc}"
        if stat.S_ISLNK(candidate_stat.st_mode):
            if candidate == root_abs:
                return f"Restore root is a symlink: {candidate.name}"
            return f"Restore parent is a symlink: {candidate.name}"
        if not stat.S_ISDIR(candidate_stat.st_mode):
            if candidate == root_abs:
                return f"Restore root is not a directory: {candidate.name}"
            return f"Restore parent is not a directory: {candidate.name}"

    return None


def _restore_target_for_arcname(
    arcname: str,
    *,
    layout: InstanceLayout,
    profile: str = ARCHIVE_PROFILE_REGULAR,
) -> tuple[Path, Path, str] | str:
    if not _is_safe_manifest_arcname(arcname):
        return f"Unsafe archive path cannot be restored: {arcname!r}"

    asset = DATA_CATALOG.find_for_logical_path(
        arcname,
        profile=profile,
    )
    if asset is None:
        return f"Unsupported restore path: {arcname}"

    layout = layout
    resolved = asset.restore_target(layout, arcname)
    if resolved is None:
        return f"Unsupported restore path: {arcname}"
    problem = _restore_parent_precondition_problem(
        resolved.path,
        resolved.scope_root,
    )
    if problem is not None:
        return f"{problem} ({arcname})"
    return resolved.path, resolved.scope_root, arcname


def _restore_action_for_target(target: Path) -> tuple[str, str | None]:
    try:
        path_stat = os.lstat(target)
    except FileNotFoundError:
        return "create", None
    except OSError as exc:
        return "blocked", f"Restore target cannot be inspected: {target.name}: {exc}"

    if stat.S_ISLNK(path_stat.st_mode):
        return "blocked", f"Restore target is a symlink: {target.name}"
    if stat.S_ISREG(path_stat.st_mode):
        return "overwrite", None
    return "blocked", f"Restore target is not a regular file: {target.name}"


def _absolute_path(path: Path) -> Path:
    return Path(os.path.abspath(path))


def _fsync_file(path: Path) -> None:
    """Persist a completed file before publishing it with ``os.replace``."""
    # Windows requires a writable handle for FlushFileBuffers (used by
    # ``os.fsync``), even though this helper never changes the contents.
    with path.open("r+b") as handle:
        os.fsync(handle.fileno())


def _fsync_directory(directory: Path) -> None:
    if os.name == "nt":
        return
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _remove_sqlite_sidecars(target: Path, root: Path) -> None:
    """Remove stale WAL/SHM files after changing a managed SQLite main database."""
    _ensure_restore_parent(target.parent, root)
    for suffix in ("-wal", "-shm"):
        sidecar = target.with_name(f"{target.name}{suffix}")
        try:
            sidecar_stat = os.lstat(sidecar)
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise ArchiveError(f"SQLite sidecar cannot be inspected: {sidecar.name}") from exc
        if stat.S_ISLNK(sidecar_stat.st_mode):
            raise ArchiveError(f"SQLite sidecar is a symlink: {sidecar.name}")
        if not stat.S_ISREG(sidecar_stat.st_mode):
            raise ArchiveError(f"SQLite sidecar is not a regular file: {sidecar.name}")
        try:
            sidecar.unlink()
        except OSError as exc:
            raise ArchiveError(f"SQLite sidecar cannot be removed: {sidecar.name}") from exc
    _fsync_directory(target.parent)


def _ensure_restore_parent(parent: Path, root: Path) -> None:
    """Create missing restore parents without crossing symlink directories."""
    root_abs = _absolute_path(root)
    parent_abs = _absolute_path(parent)
    try:
        if parent_abs != root_abs and not parent_abs.is_relative_to(root_abs):
            raise ArchiveError(f"Restore parent escapes allowed root: {parent}")
    except ValueError as exc:
        raise ArchiveError(f"Restore parent escapes allowed root: {parent}") from exc

    paths_to_check: list[Path] = []
    current = parent_abs
    while True:
        paths_to_check.append(current)
        if current == root_abs:
            break
        if current == current.parent:
            raise ArchiveError(f"Restore parent escapes allowed root: {parent}")
        current = current.parent
    paths_to_check.reverse()

    if not root_abs.exists():
        root_parent = root_abs.parent
        try:
            root_parent_stat = os.lstat(root_parent)
        except OSError as exc:
            raise ArchiveError(
                f"Restore root parent is not available: {root_parent.name}"
            ) from exc
        if stat.S_ISLNK(root_parent_stat.st_mode) or not stat.S_ISDIR(root_parent_stat.st_mode):
            raise ArchiveError(
                f"Restore root parent is not a real directory: {root_parent.name}"
            )

    for candidate in paths_to_check:
        try:
            candidate_stat = os.lstat(candidate)
        except FileNotFoundError:
            try:
                candidate.mkdir()
            except FileExistsError:
                candidate_stat = os.lstat(candidate)
            else:
                candidate_stat = os.lstat(candidate)
                _fsync_directory(candidate.parent)
        except OSError as exc:
            raise ArchiveError(f"Restore parent cannot be inspected: {candidate.name}") from exc

        if stat.S_ISLNK(candidate_stat.st_mode):
            raise ArchiveError(f"Restore parent is a symlink: {candidate.name}")
        if not stat.S_ISDIR(candidate_stat.st_mode):
            raise ArchiveError(f"Restore parent is not a directory: {candidate.name}")


def _write_zip_payload_to_target(
    archive: zipfile.ZipFile,
    arcname: str,
    *,
    target: Path,
    root: Path,
    clear_sqlite_sidecars: bool = False,
) -> int:
    """Atomically restore one verified zip payload to a regular target path."""
    _ensure_restore_parent(target.parent, root)
    action, problem = _restore_action_for_target(target)
    if action not in {"create", "overwrite"}:
        raise ArchiveError(problem or f"Restore target is blocked: {target.name}")

    tmp = target.with_name(f".{target.name}.restore-{uuid4().hex}.tmp")
    bytes_written = 0
    try:
        with archive.open(arcname, "r") as source:
            with tmp.open("xb") as handle:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    bytes_written += len(chunk)
                    handle.write(chunk)
                handle.flush()
                os.fsync(handle.fileno())

        _ensure_restore_parent(target.parent, root)
        action, problem = _restore_action_for_target(target)
        if action not in {"create", "overwrite"}:
            raise ArchiveError(problem or f"Restore target is blocked: {target.name}")
        os.replace(tmp, target)
        if clear_sqlite_sidecars:
            _remove_sqlite_sidecars(target, root)
        _fsync_directory(target.parent)
    except Exception:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
        raise
    return bytes_written


def _restore_plan_is_blocked(plan: dict) -> bool:
    return bool(plan.get("problems")) or any(
        entry.get("action") == "blocked"
        for entry in plan.get("entries", [])
        if isinstance(entry, dict)
    )


def plan_archive_restore(
    filename: str,
    *,
    layout: InstanceLayout,
    archive_dir: Path | None = None,
) -> dict:
    """Return a read-only restore plan for a verified archive."""
    with _open_existing_archive(
        filename,
        layout=layout,
        archive_dir=archive_dir,
    ) as (path, stat_info, archive):
        structure = _validate_zip_structure(archive)
        if structure:
            raise ArchiveInvalidError("; ".join(structure))
        manifest = _read_manifest_from_open_archive(archive)
        archive_summary_data = _archive_summary_from_manifest(path, stat_info, manifest)
        verification = _verify_open_archive(archive, archive_summary_data, manifest)
        return _plan_open_archive_restore(archive, verification, layout=layout)


def _plan_open_archive_restore(
    archive: zipfile.ZipFile,
    verification: dict,
    *,
    layout: InstanceLayout,
) -> dict:
    if not verification.get("verified"):
        raise ArchiveRestorePlanVerificationError(verification)
    profile = verification.get("profile", ARCHIVE_PROFILE_REGULAR)

    sizes: dict[str, int] = {}
    for arcname in verification["restorable_files"]:
        try:
            sizes[arcname] = archive.getinfo(arcname).file_size
        except KeyError:
            sizes[arcname] = 0

    entries: list[dict] = []
    problems: list[str] = []
    warnings = list(verification.get("warnings", []))
    declared_asset_ids = set(verification.get("declared_asset_ids", []))
    for arcname in verification["restorable_files"]:
        mapped = _restore_target_for_arcname(arcname, layout=layout, profile=profile)
        if isinstance(mapped, str):
            problems.append(mapped)
            continue
        target, _root, display_path = mapped
        action, problem = _restore_action_for_target(target)
        entry = {
            "arcname": arcname,
            "target_path": display_path,
            "action": action,
            "size": sizes.get(arcname, 0),
            "asset_id": (
                DATA_CATALOG.find_for_logical_path(arcname, profile=profile).id
            ),
        }
        entries.append(entry)
        if problem is not None:
            problems.append(f"{problem} ({display_path})")

    archived = set(verification["restorable_files"])
    for current in DATA_CATALOG.collect(layout, profile):
        if current.logical_path in archived:
            continue
        asset = DATA_CATALOG.find_for_logical_path(
            current.logical_path,
            profile=profile,
        )
        if asset is None:
            continue
        if not declared_asset_ids or asset.id not in declared_asset_ids:
            # v1 has no asset declaration, and old v2 archives may predate
            # assets introduced by the current program. Preserve both.
            continue
        mapped = asset.restore_target(layout, current.logical_path)
        if mapped is None:
            problems.append(f"Current managed path cannot be mapped: {current.logical_path}")
            continue
        action, problem = _restore_action_for_target(mapped.path)
        if action == "overwrite":
            entries.append(
                {
                    "arcname": None,
                    "target_path": current.logical_path,
                    "action": "remove",
                    "size": current.path.stat().st_size,
                    "asset_id": asset.id,
                }
            )
        elif problem is not None:
            entries.append(
                {
                    "arcname": None,
                    "target_path": current.logical_path,
                    "action": "blocked",
                    "size": 0,
                    "asset_id": asset.id,
                }
            )
            problems.append(f"{problem} ({current.logical_path})")

    return {
        "archive": verification["archive"],
        "verified": True,
        "entries": entries,
        "problems": problems,
        "warnings": warnings,
        "opaque_sqlite": verification.get(
            "opaque_sqlite",
            {"count": 0, "files": []},
        ),
        "profile": profile,
        "create": [entry for entry in entries if entry["action"] == "create"],
        "overwrite": [entry for entry in entries if entry["action"] == "overwrite"],
        "remove": [entry for entry in entries if entry["action"] == "remove"],
        "blocked": [entry for entry in entries if entry["action"] == "blocked"],
    }


def restore_archive(
    filename: str,
    description: str | None = None,
    *,
    layout: InstanceLayout,
    archive_dir: Path | None = None,
) -> dict:
    """Compatibility wrapper: create a safety archive, then apply the target."""
    plan = plan_archive_restore(filename, layout=layout, archive_dir=archive_dir)
    if _restore_plan_is_blocked(plan):
        raise ArchiveRestorePlanBlockedError(plan)
    profile = plan["profile"]
    pre_restore_archive, pre_restore_manifest = create_archive(
        description=description or f"pre-restore {filename}",
        layout=layout,
        archive_dir=archive_dir,
        profile=profile,
        archive_kind="system",
    )
    result = apply_archive(filename, layout=layout, archive_dir=archive_dir)
    result["pre_restore_archive"] = pre_restore_archive
    result["pre_restore_manifest"] = pre_restore_manifest
    return result


def _remove_restore_target(target: Path, root: Path) -> None:
    problem = _restore_parent_precondition_problem(target, root)
    if problem is not None:
        raise ArchiveError(problem)
    action, problem = _restore_action_for_target(target)
    if action != "overwrite":
        raise ArchiveError(problem or f"Restore removal target is blocked: {target.name}")
    target.unlink()
    _fsync_directory(target.parent)


def apply_archive(
    filename: str,
    *,
    layout: InstanceLayout,
    archive_dir: Path | None = None,
    phase_callback=None,
) -> dict:
    """Apply a verified archive without creating a safety point.

    Only the Manager coordinator should call this primitive during a durable
    restore transaction.
    """
    with _open_existing_archive(
        filename,
        layout=layout,
        archive_dir=archive_dir,
    ) as (path, stat_info, archive):
        structure = _validate_zip_structure(archive)
        if structure:
            raise ArchiveInvalidError("; ".join(structure))
        manifest = _read_manifest_from_open_archive(archive)
        archive_summary_data = _archive_summary_from_manifest(path, stat_info, manifest)
        verification = _verify_open_archive(archive, archive_summary_data, manifest)
        plan = _plan_open_archive_restore(archive, verification, layout=layout)
        if _restore_plan_is_blocked(plan):
            raise ArchiveRestorePlanBlockedError(plan)

        restored_entries: list[dict] = []
        failed_entries: list[dict] = []
        profile = plan["profile"]

        try:
            for entry in plan["entries"]:
                if entry.get("action") not in {"create", "overwrite"}:
                    continue
                arcname = entry["arcname"]
                mapped = _restore_target_for_arcname(
                    arcname,
                    layout=layout,
                    profile=profile,
                )
                if isinstance(mapped, str):
                    failed_entries.append({
                        **entry,
                        "error": mapped,
                    })
                    break
                target, root, _display_path = mapped
                asset = DATA_CATALOG.find_for_logical_path(
                    arcname,
                    profile=profile,
                )
                try:
                    bytes_written = _write_zip_payload_to_target(
                        archive,
                        arcname,
                        target=target,
                        root=root,
                        clear_sqlite_sidecars=(
                            asset is not None and asset.kind is DataAssetKind.SQLITE
                        ),
                    )
                except Exception as exc:
                    failed_entries.append({
                        **entry,
                        "error": str(exc) or type(exc).__name__,
                    })
                    break
                restored_entries.append({
                    **entry,
                    "bytes_written": bytes_written,
                })
                if phase_callback is not None:
                    phase_callback("write", entry)
            if not failed_entries:
                for entry in plan["entries"]:
                    if entry.get("action") != "remove":
                        continue
                    logical_path = entry["target_path"]
                    asset = DATA_CATALOG.find_for_logical_path(
                        logical_path,
                        profile=profile,
                    )
                    mapped = (
                        asset.restore_target(layout, logical_path)
                        if asset is not None
                        else None
                    )
                    if mapped is None:
                        failed_entries.append(
                            {**entry, "error": "Removal target is outside the profile"}
                        )
                        break
                    try:
                        _remove_restore_target(mapped.path, mapped.scope_root)
                        if asset.kind is DataAssetKind.SQLITE:
                            _remove_sqlite_sidecars(mapped.path, mapped.scope_root)
                    except Exception as exc:
                        failed_entries.append(
                            {**entry, "error": str(exc) or type(exc).__name__}
                        )
                        break
                    restored_entries.append({**entry, "bytes_written": 0})
                    if phase_callback is not None:
                        phase_callback("delete", entry)
        except zipfile.BadZipFile as exc:
            raise ArchiveInvalidError("Archive zip cannot be read") from exc

        return {
            "archive": plan["archive"],
            "restored_entries": restored_entries,
            "failed_entries": failed_entries,
            "plan": plan,
        }


def delete_archive(
    filename: str,
    *,
    layout: InstanceLayout,
    archive_dir: Path | None = None,
) -> dict:
    """Delete a regular archive zip and return its summary."""
    path = _existing_regular_archive_path(filename, layout=layout, archive_dir=archive_dir)
    summary = archive_summary(path)
    try:
        path.unlink()
    except FileNotFoundError as exc:
        raise ArchiveNotFoundError(f"Archive not found: {filename}") from exc
    return summary


def list_archives(
    *,
    layout: InstanceLayout,
    archive_dir: Path | None = None,
) -> list[dict]:
    """List local zip archive summaries, newest first."""
    target_dir = archive_dir or backups_dir(layout)
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
    archive_dir: Path | None = None,
    max_bytes: int = MAX_ARCHIVE_BYTES,
) -> dict:
    """Stream an uploaded archive into Manager storage and verify before publish."""
    safe_archive_path(filename, layout=layout, archive_dir=archive_dir)
    target_dir = archive_dir or backups_dir(layout)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = safe_archive_path(filename, layout=layout, archive_dir=target_dir)
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
            os.fsync(handle.fileno())
        verification = verify_archive_path(tmp, expected_filename=target.name)
        if not verification["verified"]:
            raise ArchiveInvalidError(
                "Imported archive verification failed: "
                + "; ".join(verification["problems"])
            )
        os.replace(tmp, target)
        _fsync_directory(target.parent)
        _fsync_directory(target_dir)
        return {
            "archive": archive_summary(target),
            "verification": verification,
            "restored": False,
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
    archive_dir: Path | None = None,
) -> Path:
    """Return a verified regular file path for the Manager HTTP download route."""
    path = _existing_regular_archive_path(
        filename,
        layout=layout,
        archive_dir=archive_dir,
    )
    verification = verify_archive(filename, layout=layout, archive_dir=archive_dir)
    if not verification["verified"]:
        raise ArchiveInvalidError("Archive cannot be exported because verification failed")
    return path


def enforce_system_retention(
    *,
    layout: InstanceLayout,
    keep: int = 5,
    protected: set[str] | None = None,
    archive_dir: Path | None = None,
) -> list[str]:
    """Remove only old system safety archives; manual archives are never pruned."""
    protected_names = protected or set()
    system_archives = [
        item
        for item in list_archives(layout=layout, archive_dir=archive_dir)
        if item.get("archive_kind") == "system"
        and item.get("filename") not in protected_names
    ]
    deleted: list[str] = []
    for item in system_archives[keep:]:
        filename = str(item["filename"])
        delete_archive(filename, layout=layout, archive_dir=archive_dir)
        deleted.append(filename)
    return deleted
