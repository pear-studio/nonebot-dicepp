"""Local Dashboard save archive creation and listing."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Iterator
from uuid import uuid4

from .config import DashboardPaths
from .manager.models import get_dicepp_version

ARCHIVE_FORMAT_VERSION = 1
MANIFEST_NAME = "manifest.json"
CHECKSUM_ALGORITHM = "sha256"

SCOPE_INCLUDED = [
    "config/user.json",
    "config/bots/*.json",
    "data/dicepp.db",
    "data/bots/*/bot_data.db",
    "data/bots/*/log.db",
    "data/bots/*/personas_data_*.db",
    "data/local_images",
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


def backups_dir(paths: type[DashboardPaths] = DashboardPaths) -> Path:
    """Return the default Dashboard archive directory."""
    return paths.DATA_ROOT / "backups"


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


def _iter_regular_files(root: Path, arc_prefix: str) -> list[ArchivePayload]:
    """Return ordinary files under *root* without following symlink directories."""
    if not root.exists() or root.is_symlink() or not root.is_dir():
        return []

    payloads: list[ArchivePayload] = []
    for current, dirnames, filenames in os.walk(root, followlinks=False):
        current_path = Path(current)
        dirnames[:] = [
            name for name in dirnames
            if not (current_path / name).is_symlink()
        ]
        for filename in filenames:
            path = current_path / filename
            if path.is_symlink() or not path.is_file():
                continue
            rel = path.relative_to(root).as_posix()
            payloads.append(ArchivePayload(path=path, arcname=_safe_arcname(f"{arc_prefix}/{rel}")))
    return sorted(payloads, key=lambda item: item.arcname)


def _regular_file(path: Path, arcname: str) -> list[ArchivePayload]:
    if not path.exists() or path.is_symlink() or not path.is_file():
        return []
    return [ArchivePayload(path=path, arcname=_safe_arcname(arcname))]


def _iter_config_payloads(paths: type[DashboardPaths]) -> list[ArchivePayload]:
    payloads: list[ArchivePayload] = []
    payloads.extend(_regular_file(Path(paths.CONFIG_USER), "config/user.json"))

    bots_root = Path(paths.CONFIG_BOTS_DIR)
    if bots_root.exists() and not bots_root.is_symlink() and bots_root.is_dir():
        for bot_config in sorted(bots_root.glob("*.json"), key=lambda item: item.name):
            if bot_config.name == "_template.json":
                continue
            payloads.extend(
                _regular_file(bot_config, f"config/bots/{bot_config.name}")
            )
    return payloads


def collect_archive_payloads(
    paths: type[DashboardPaths] = DashboardPaths,
) -> list[ArchivePayload]:
    """Collect files included in the first save-archive format."""
    data_root = Path(paths.DATA_BOTS_DIR).parent
    payloads: list[ArchivePayload] = []
    payloads.extend(_iter_config_payloads(paths))
    payloads.extend(_regular_file(data_root / "dicepp.db", "data/dicepp.db"))

    bots_root = Path(paths.DATA_BOTS_DIR)
    if bots_root.exists() and not bots_root.is_symlink() and bots_root.is_dir():
        for bot_dir in sorted(bots_root.iterdir(), key=lambda item: item.name):
            if bot_dir.is_symlink() or not bot_dir.is_dir():
                continue
            prefix = f"data/bots/{bot_dir.name}"
            payloads.extend(_regular_file(bot_dir / "bot_data.db", f"{prefix}/bot_data.db"))
            payloads.extend(_regular_file(bot_dir / "log.db", f"{prefix}/log.db"))
            for persona_db in sorted(bot_dir.glob("personas_data_*.db")):
                payloads.extend(_regular_file(persona_db, f"{prefix}/{persona_db.name}"))

    payloads.extend(_iter_regular_files(data_root / "local_images", "data/local_images"))
    return sorted(payloads, key=lambda item: item.arcname)


def _archive_filename(now: datetime, description: str | None) -> str:
    slug = sanitize_description_slug(description)
    suffix = f"-{slug}" if slug else ""
    return f"{_filename_timestamp(now)}{suffix}-{uuid4().hex[:8]}.zip"


def _build_manifest(
    *,
    created_at: str,
    description: str,
    checksums: dict[str, str],
) -> dict:
    return {
        "format_version": ARCHIVE_FORMAT_VERSION,
        "created_at": created_at,
        "dicepp_version": get_dicepp_version(),
        "description": description,
        "scope": {
            "included": SCOPE_INCLUDED,
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
) -> str | None:
    """Write one payload and return the sha256 of the bytes written."""
    source = _open_regular_payload(payload.path)
    if source is None:
        return None

    digest = hashlib.sha256()
    with source:
        with archive.open(payload.arcname, "w") as target:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
                target.write(chunk)
    return digest.hexdigest()


def create_archive(
    description: str | None = None,
    *,
    paths: type[DashboardPaths] = DashboardPaths,
    archive_dir: Path | None = None,
) -> tuple[dict, dict]:
    """Create a local zip archive and return ``(summary, manifest)``."""
    target_dir = archive_dir or backups_dir(paths)
    target_dir.mkdir(parents=True, exist_ok=True)
    now = _utc_now()
    filename = _archive_filename(now, description)
    target = target_dir / filename
    tmp = target.with_name(f".{target.name}.tmp")
    payloads = collect_archive_payloads(paths)
    checksums: dict[str, str] = {}
    manifest: dict | None = None

    try:
        with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for payload in payloads:
                checksum = _write_payload_to_archive(archive, payload)
                if checksum is not None:
                    checksums[payload.arcname] = checksum
            manifest = _build_manifest(
                created_at=_format_created_at(now),
                description=description or "",
                checksums=checksums,
            )
            archive.writestr(
                MANIFEST_NAME,
                json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
            )
        os.replace(tmp, target)
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
    paths: type[DashboardPaths] = DashboardPaths,
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

    target_dir = archive_dir or backups_dir(paths)
    return target_dir / filename


def _existing_regular_archive_path(
    filename: str,
    *,
    paths: type[DashboardPaths] = DashboardPaths,
    archive_dir: Path | None = None,
) -> Path:
    path = safe_archive_path(filename, paths=paths, archive_dir=archive_dir)
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
    paths: type[DashboardPaths] = DashboardPaths,
    archive_dir: Path | None = None,
) -> Iterator[tuple[Path, os.stat_result, zipfile.ZipFile]]:
    """Open one regular archive file and keep the fd through the operation."""
    path = safe_archive_path(filename, paths=paths, archive_dir=archive_dir)
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
            with archive.open(MANIFEST_NAME) as handle:
                manifest = json.load(handle)
    except (OSError, KeyError, json.JSONDecodeError, zipfile.BadZipFile):
        return summary

    if isinstance(manifest, dict):
        summary["valid"] = True
        summary["created_at"] = manifest.get("created_at") or fallback_created_at
        summary["description"] = manifest.get("description", "")
        summary["format_version"] = manifest.get("format_version")
        summary["dicepp_version"] = manifest.get("dicepp_version", "")
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
        "file_count": len(files) if isinstance(files, dict) else 0,
    }


def _read_manifest_from_open_archive(archive: zipfile.ZipFile) -> dict:
    try:
        with archive.open(MANIFEST_NAME) as handle:
            manifest = json.load(handle)
    except KeyError as exc:
        raise ArchiveInvalidError("Archive manifest is missing") from exc
    except (OSError, json.JSONDecodeError, zipfile.BadZipFile) as exc:
        raise ArchiveInvalidError("Archive manifest cannot be read") from exc

    if not isinstance(manifest, dict):
        raise ArchiveInvalidError("Archive manifest must be a JSON object")
    return manifest


def read_archive_detail(
    filename: str,
    *,
    paths: type[DashboardPaths] = DashboardPaths,
    archive_dir: Path | None = None,
) -> tuple[dict, dict]:
    """Return ``(summary, manifest)`` for a regular archive zip filename."""
    with _open_existing_archive(
        filename,
        paths=paths,
        archive_dir=archive_dir,
    ) as (path, stat_info, archive):
        manifest = _read_manifest_from_open_archive(archive)
        return _archive_summary_from_manifest(path, stat_info, manifest), manifest


def _validate_manifest_for_verify(manifest: dict) -> dict[str, str]:
    if manifest.get("format_version") != ARCHIVE_FORMAT_VERSION:
        raise ArchiveInvalidError("Unsupported archive format version")
    checksum = manifest.get("checksum")
    if not isinstance(checksum, dict):
        raise ArchiveInvalidError("Archive manifest checksum must be an object")
    if checksum.get("algorithm") != CHECKSUM_ALGORITHM:
        raise ArchiveInvalidError("Unsupported archive checksum algorithm")
    files = checksum.get("files")
    if not isinstance(files, dict):
        raise ArchiveInvalidError("Archive manifest checksum.files must be an object")
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


def _archive_arcname_in_restore_scope(arcname: str) -> bool:
    if arcname == "data/dicepp.db" or arcname == "config/user.json":
        return True

    if arcname.startswith("config/bots/"):
        relative = PurePosixPath(arcname.removeprefix("config/bots/"))
        return (
            len(relative.parts) == 1
            and relative.name != "_template.json"
            and relative.suffix == ".json"
            and not relative.is_absolute()
            and ".." not in relative.parts
        )

    if arcname.startswith("data/bots/"):
        relative = PurePosixPath(arcname.removeprefix("data/bots/"))
        if len(relative.parts) != 2 or relative.is_absolute() or ".." in relative.parts:
            return False
        filename = relative.parts[1]
        return filename in {"bot_data.db", "log.db"} or (
            filename.startswith("personas_data_") and filename.endswith(".db")
        )

    if arcname.startswith("data/local_images/"):
        relative = PurePosixPath(arcname.removeprefix("data/local_images/"))
        return (
            bool(relative.parts)
            and relative.as_posix() not in {"", "."}
            and not relative.is_absolute()
            and ".." not in relative.parts
        )

    return False


def verify_archive(
    filename: str,
    *,
    paths: type[DashboardPaths] = DashboardPaths,
    archive_dir: Path | None = None,
) -> dict:
    """Verify a save archive manifest and payload checksums without restoring."""
    with _open_existing_archive(
        filename,
        paths=paths,
        archive_dir=archive_dir,
    ) as (path, stat_info, archive):
        manifest = _read_manifest_from_open_archive(archive)
        archive_summary_data = _archive_summary_from_manifest(path, stat_info, manifest)
        return _verify_open_archive(archive, archive_summary_data, manifest)


def _verify_open_archive(
    archive: zipfile.ZipFile,
    archive_summary_data: dict,
    manifest: dict,
) -> dict:
    checksum_files = _validate_manifest_for_verify(manifest)

    problems: list[str] = []
    warnings: list[str] = []
    restorable_files: list[str] = []

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
            if not _archive_arcname_in_restore_scope(arcname):
                problems.append(f"Unsupported restore path: {arcname}")
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
            warnings.append(f"Zip contains undeclared payload file: {arcname}")
    except zipfile.BadZipFile as exc:
        raise ArchiveInvalidError("Archive zip cannot be read") from exc

    return {
        "archive": archive_summary_data,
        "manifest": manifest,
        "verified": not problems,
        "problems": problems,
        "warnings": warnings,
        "restorable_files": sorted(restorable_files),
    }


def _data_root(paths: type[DashboardPaths]) -> Path:
    return Path(paths.DATA_BOTS_DIR).parent


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
    paths: type[DashboardPaths],
) -> tuple[Path, Path, str] | str:
    if not _is_safe_manifest_arcname(arcname):
        return f"Unsafe archive path cannot be restored: {arcname!r}"

    data_root = _data_root(paths)
    if arcname == "data/dicepp.db":
        target = data_root / "dicepp.db"
        root = data_root
        problem = _restore_parent_precondition_problem(target, root)
        if problem is not None:
            return f"{problem} ({arcname})"
        return target, root, arcname

    if arcname == "config/user.json":
        target = Path(paths.CONFIG_USER)
        root = Path(paths.CONFIG_DIR)
        problem = _restore_parent_precondition_problem(target, root)
        if problem is not None:
            return f"{problem} ({arcname})"
        return target, root, arcname

    if arcname.startswith("config/bots/"):
        relative = PurePosixPath(arcname.removeprefix("config/bots/"))
        if (
            len(relative.parts) != 1
            or relative.name == "_template.json"
            or relative.suffix != ".json"
            or relative.is_absolute()
            or ".." in relative.parts
        ):
            return f"Unsupported restore path: {arcname}"
        target = Path(paths.CONFIG_BOTS_DIR) / relative.name
        root = Path(paths.CONFIG_BOTS_DIR)
        problem = _restore_parent_precondition_problem(target, root)
        if problem is not None:
            return f"{problem} ({arcname})"
        return target, root, arcname

    if arcname.startswith("data/bots/"):
        relative = PurePosixPath(arcname.removeprefix("data/bots/"))
        if len(relative.parts) != 2 or relative.is_absolute() or ".." in relative.parts:
            return f"Unsupported restore path: {arcname}"
        filename = relative.parts[1]
        if filename not in {"bot_data.db", "log.db"} and not (
            filename.startswith("personas_data_") and filename.endswith(".db")
        ):
            return f"Unsupported restore path: {arcname}"
        target = Path(paths.DATA_BOTS_DIR).joinpath(*relative.parts)
        root = Path(paths.DATA_BOTS_DIR)
        problem = _restore_parent_precondition_problem(target, root)
        if problem is not None:
            return f"{problem} ({arcname})"
        return target, root, arcname

    if arcname.startswith("data/local_images/"):
        relative = PurePosixPath(arcname.removeprefix("data/local_images/"))
        if (
            not relative.parts
            or relative.as_posix() in {"", "."}
            or relative.is_absolute()
            or ".." in relative.parts
        ):
            return f"Archive path does not name a restorable file: {arcname}"
        target = (data_root / "local_images").joinpath(*relative.parts)
        root = data_root / "local_images"
        problem = _restore_parent_precondition_problem(target, root)
        if problem is not None:
            return f"{problem} ({arcname})"
        return target, root, f"data/local_images/{relative.as_posix()}"

    return f"Unsupported restore path: {arcname}"


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
    paths: type[DashboardPaths] = DashboardPaths,
    archive_dir: Path | None = None,
) -> dict:
    """Return a read-only restore plan for a verified archive."""
    with _open_existing_archive(
        filename,
        paths=paths,
        archive_dir=archive_dir,
    ) as (path, stat_info, archive):
        manifest = _read_manifest_from_open_archive(archive)
        archive_summary_data = _archive_summary_from_manifest(path, stat_info, manifest)
        verification = _verify_open_archive(archive, archive_summary_data, manifest)
        return _plan_open_archive_restore(archive, verification, paths=paths)


def _plan_open_archive_restore(
    archive: zipfile.ZipFile,
    verification: dict,
    *,
    paths: type[DashboardPaths],
) -> dict:
    if not verification.get("verified"):
        raise ArchiveRestorePlanVerificationError(verification)

    sizes: dict[str, int] = {}
    for arcname in verification["restorable_files"]:
        try:
            sizes[arcname] = archive.getinfo(arcname).file_size
        except KeyError:
            sizes[arcname] = 0

    entries: list[dict] = []
    problems: list[str] = []
    warnings = list(verification.get("warnings", []))
    for arcname in verification["restorable_files"]:
        mapped = _restore_target_for_arcname(arcname, paths=paths)
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
        }
        entries.append(entry)
        if problem is not None:
            problems.append(f"{problem} ({display_path})")

    return {
        "archive": verification["archive"],
        "verified": True,
        "entries": entries,
        "problems": problems,
        "warnings": warnings,
    }


def restore_archive(
    filename: str,
    description: str | None = None,
    *,
    paths: type[DashboardPaths] = DashboardPaths,
    archive_dir: Path | None = None,
) -> dict:
    """Restore verified archive payloads after first creating a pre-restore archive."""
    with _open_existing_archive(
        filename,
        paths=paths,
        archive_dir=archive_dir,
    ) as (path, stat_info, archive):
        manifest = _read_manifest_from_open_archive(archive)
        archive_summary_data = _archive_summary_from_manifest(path, stat_info, manifest)
        verification = _verify_open_archive(archive, archive_summary_data, manifest)
        plan = _plan_open_archive_restore(archive, verification, paths=paths)
        if _restore_plan_is_blocked(plan):
            raise ArchiveRestorePlanBlockedError(plan)

        pre_restore_archive, pre_restore_manifest = create_archive(
            description=description or f"pre-restore {filename}",
            paths=paths,
            archive_dir=archive_dir,
        )

        restored_entries: list[dict] = []
        failed_entries: list[dict] = []

        try:
            for entry in plan["entries"]:
                if entry.get("action") not in {"create", "overwrite"}:
                    continue
                arcname = entry["arcname"]
                mapped = _restore_target_for_arcname(arcname, paths=paths)
                if isinstance(mapped, str):
                    failed_entries.append({
                        **entry,
                        "error": mapped,
                    })
                    break
                target, root, _display_path = mapped
                try:
                    bytes_written = _write_zip_payload_to_target(
                        archive,
                        arcname,
                        target=target,
                        root=root,
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
        except zipfile.BadZipFile as exc:
            raise ArchiveInvalidError("Archive zip cannot be read") from exc

        return {
            "archive": plan["archive"],
            "pre_restore_archive": pre_restore_archive,
            "pre_restore_manifest": pre_restore_manifest,
            "restored_entries": restored_entries,
            "failed_entries": failed_entries,
            "plan": plan,
        }


def delete_archive(
    filename: str,
    *,
    paths: type[DashboardPaths] = DashboardPaths,
    archive_dir: Path | None = None,
) -> dict:
    """Delete a regular archive zip and return its summary."""
    path = _existing_regular_archive_path(filename, paths=paths, archive_dir=archive_dir)
    summary = archive_summary(path)
    try:
        path.unlink()
    except FileNotFoundError as exc:
        raise ArchiveNotFoundError(f"Archive not found: {filename}") from exc
    return summary


def list_archives(
    *,
    paths: type[DashboardPaths] = DashboardPaths,
    archive_dir: Path | None = None,
) -> list[dict]:
    """List local zip archive summaries, newest first."""
    target_dir = archive_dir or backups_dir(paths)
    if not target_dir.exists() or not target_dir.is_dir():
        return []
    archives = [
        archive_summary(path)
        for path in target_dir.iterdir()
        if not path.is_symlink() and path.is_file() and path.suffix.lower() == ".zip"
    ]
    return sorted(archives, key=lambda item: (item["created_at"], item["filename"]), reverse=True)
