"""Small, local operations for clearing and importing one DicePP instance.

This module deliberately knows only the shared :mod:`dicepp_data` catalog.  It
does not copy Dashboard state, archive inventory, runtime logs, or integration
adapter data.  Callers serialize these operations with the Dashboard data
maintenance lock and verify that the Bot is stopped before calling them.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import zipfile
from pathlib import Path

from dicepp_data import (
    ARCHIVE_PROFILE_FULL,
    DATA_CATALOG,
    DataAsset,
    DataAssetKind,
    InstanceLayout,
)
from dicepp_data.archive import (
    ArchiveError,
    MANIFEST_NAME,
    read_archive_detail,
    safe_archive_path,
    verify_archive,
)
from plugins.DicePP.core.data.schema.lifecycle import SchemaLifecycleError


INSTANCE_DATA_MARKER = ".instance-data.inprogress"


class InstanceDataError(RuntimeError):
    """Raised when a catalog-scoped instance operation cannot be completed."""


class InstanceDataNotEmptyError(InstanceDataError):
    """Raised when import is requested for a non-empty target instance."""


class InstanceDataSourceError(InstanceDataError):
    """Raised when an import source is unavailable or unsafe."""


class InstanceDataInProgressError(InstanceDataError):
    """Raised when an unfinished import marker already exists."""


def instance_data_marker_path(layout: InstanceLayout) -> Path:
    """Return the marker which blocks Bot start during an unfinished import."""
    return layout.data_root / INSTANCE_DATA_MARKER


def instance_data_is_empty(layout: InstanceLayout) -> bool:
    """Return whether any catalog-managed business asset exists."""
    return not DATA_CATALOG.collect(layout, ARCHIVE_PROFILE_FULL)


def clear_instance_data(layout: InstanceLayout) -> dict[str, object]:
    """Delete exactly the files owned by the full DataAsset catalog.

    Empty directories are intentionally left in place.  This keeps the normal
    project layout stable while ensuring Dashboard state, archives, and logs
    outside the catalog are untouched.
    """
    removed: list[str] = []
    for match in DATA_CATALOG.collect(layout, ARCHIVE_PROFILE_FULL):
        _remove_managed_path(match.path, match.logical_path, removed)
        asset = DATA_CATALOG.find_for_logical_path(
            match.logical_path, profile=ARCHIVE_PROFILE_FULL
        )
        if asset is not None and asset.kind is DataAssetKind.SQLITE:
            for suffix in ("-wal", "-shm"):
                sidecar = match.path.with_name(match.path.name + suffix)
                _remove_managed_path(sidecar, f"{match.logical_path}{suffix}", removed)
    marker = instance_data_marker_path(layout)
    try:
        marker.unlink()
    except FileNotFoundError:
        pass
    return {"cleared": sorted(removed), "count": len(removed)}


def import_instance_data(
    layout: InstanceLayout,
    *,
    archive: str | None = None,
    source_root: str | os.PathLike[str] | None = None,
) -> dict[str, object]:
    """Import catalog assets into an empty target and migrate SQLite forward."""
    if archive is not None and source_root is not None:
        raise InstanceDataSourceError("Choose an archive or source_path, not both")
    if archive is None and source_root is None:
        raise InstanceDataSourceError("An archive or source_path is required")
    if isinstance(source_root, str) and not source_root.strip():
        raise InstanceDataSourceError("source_path must be a non-empty directory path")
    if not instance_data_is_empty(layout):
        raise InstanceDataNotEmptyError(
            "Business data is not empty; clear the instance before importing"
        )
    marker = instance_data_marker_path(layout)
    if marker.exists():
        raise InstanceDataInProgressError(
            "An instance-data import is already in progress; clear and retry"
        )
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("importing\n", encoding="utf-8")

    imported: list[str] = []
    migrated: list[dict[str, object]] = []
    try:
        if archive is not None:
            imported = _import_archive(layout, archive)
        else:
            imported = _import_directory(layout, Path(source_root))
        for logical_path in imported:
            asset = DATA_CATALOG.find_for_logical_path(
                logical_path, profile=ARCHIVE_PROFILE_FULL
            )
            if asset is None or asset.schema is None:
                continue
            result = _migrate_schema(
                _target_for_logical_path(layout, logical_path), asset
            )
            migrated.append(
                {
                    "path": logical_path,
                    "current_version": result.current_version,
                    "target_version": result.target_version,
                    "applied_versions": result.applied_versions,
                    "created": result.created,
                }
            )
    except (ArchiveError, OSError, sqlite3.Error, ValueError, SchemaLifecycleError) as exc:
        # The marker intentionally remains.  The user must clear and retry;
        # there is no hidden rollback or automatic restart path.
        raise InstanceDataSourceError(str(exc) or type(exc).__name__) from exc
    else:
        marker.unlink(missing_ok=True)

    return {"imported": sorted(imported), "count": len(imported), "migrations": migrated}


def _import_directory(layout: InstanceLayout, source_root: Path) -> list[str]:
    source_root = source_root.expanduser().resolve()
    if not source_root.exists() or not source_root.is_dir():
        raise InstanceDataSourceError("source_path must be an existing directory")
    target_root = layout.root.resolve()
    try:
        source_root.relative_to(target_root)
    except ValueError:
        pass
    else:
        raise InstanceDataSourceError("source_path must not be the target instance or one of its children")
    source_layout = InstanceLayout.from_root(source_root)
    imported: list[str] = []
    for match in DATA_CATALOG.collect(source_layout, ARCHIVE_PROFILE_FULL):
        target = _target_for_logical_path(layout, match.logical_path)
        _copy_file(match.path, target, match.logical_path)
        imported.append(match.logical_path)
    return imported


def _import_archive(layout: InstanceLayout, filename: str) -> list[str]:
    try:
        verification = verify_archive(filename, layout=layout)
        if not verification.get("verified"):
            problems = "; ".join(verification.get("problems") or [])
            raise InstanceDataSourceError(
                "Archive verification failed" + (f": {problems}" if problems else "")
            )
        _summary, manifest = read_archive_detail(filename, layout=layout)
        path = safe_archive_path(filename, layout=layout)
    except ArchiveError:
        raise
    files = manifest.get("checksum", {}).get("files", {})
    if not isinstance(files, dict):
        raise InstanceDataSourceError("Archive manifest checksum is missing")
    imported: list[str] = []
    with zipfile.ZipFile(path, "r") as archive:
        for logical_path in sorted(files):
            if logical_path == MANIFEST_NAME:
                continue
            asset = DATA_CATALOG.find_for_logical_path(
                logical_path, profile=ARCHIVE_PROFILE_FULL
            )
            if asset is None:
                continue
            target = _target_for_logical_path(layout, logical_path)
            with archive.open(logical_path, "r") as source:
                _copy_stream(source, target, logical_path)
            imported.append(logical_path)
    return imported


def _target_for_logical_path(layout: InstanceLayout, logical_path: str) -> Path:
    asset = DATA_CATALOG.find_for_logical_path(
        logical_path, profile=ARCHIVE_PROFILE_FULL
    )
    if asset is None:
        raise InstanceDataSourceError(f"Unsupported catalog path: {logical_path}")
    target = asset.restore_target(layout, logical_path)
    if target is None:
        raise InstanceDataSourceError(f"Unsafe catalog path: {logical_path}")
    root = layout.area_root(asset.area).resolve()
    resolved = target.path.resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise InstanceDataSourceError(f"Catalog path escapes instance: {logical_path}") from exc
    if target.path.exists() and target.path.is_symlink():
        raise InstanceDataSourceError(f"Target path is a symlink: {logical_path}")
    return target.path


def _copy_file(source: Path, target: Path, logical_path: str) -> None:
    if source.is_symlink() or not source.is_file():
        raise InstanceDataSourceError(f"Source is not a regular file: {logical_path}")
    with source.open("rb") as handle:
        _copy_stream(handle, target, logical_path)


def _copy_stream(source, target: Path, logical_path: str) -> None:
    if target.exists():
        raise InstanceDataSourceError(f"Target already exists: {logical_path}")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.parent.is_symlink():
        raise InstanceDataSourceError(f"Target directory is a symlink: {logical_path}")
    with target.open("xb") as handle:
        shutil.copyfileobj(source, handle, length=1024 * 1024)


def _remove_managed_path(path: Path, logical_path: str, removed: list[str]) -> None:
    if path.is_symlink():
        raise InstanceDataError(f"Refusing to remove symlink: {logical_path}")
    try:
        path.unlink()
    except FileNotFoundError:
        return
    removed.append(logical_path)


def _migrate_schema(path: Path, asset: DataAsset):
    from plugins.DicePP.core.data.schema import (
        BOT_CORE_TARGET,
        INSTANCE_TARGET,
        ensure_bot_log_schema,
        apply_schema_target,
    )
    if asset.id == "data.bot_log":
        return ensure_bot_log_schema(path)
    if asset.id == "data.instance":
        return apply_schema_target(path, INSTANCE_TARGET)
    if asset.id == "data.bot_core":
        return apply_schema_target(path, BOT_CORE_TARGET)
    if asset.id == "data.persona":
        from plugins.DicePP.module.persona.data.schema import PERSONA_TARGET

        return apply_schema_target(path, PERSONA_TARGET)
    raise InstanceDataError(f"No schema migration target for {asset.id}")


__all__ = [
    "INSTANCE_DATA_MARKER",
    "InstanceDataError",
    "InstanceDataNotEmptyError",
    "InstanceDataInProgressError",
    "InstanceDataSourceError",
    "clear_instance_data",
    "import_instance_data",
    "instance_data_is_empty",
    "instance_data_marker_path",
]
