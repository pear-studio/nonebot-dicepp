"""Confirmed, durable DicePP program upgrade transactions.

The coordinator owns data safety and transaction recovery.  Platform adapters
own only the program switch: Docker images on Linux and the UpdateGuard /
Velopack hand-off on Windows.  External services are deliberately outside the
hard-health boundary.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import secrets
import shutil
import stat
import subprocess
import tempfile
import zipfile
import xml.etree.ElementTree as ElementTree
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Protocol
from uuid import uuid4

from dicepp_data import DATA_CATALOG, InstanceLayout
from dicepp_meta import get_version
from packaging.version import Version

from .archive import ArchiveError, apply_archive, create_archive, estimate_archive
from .archive_coordinator import CONTROL_GATE_ENFORCED, ArchiveCoordinator
from ._file_utils import _atomic_copy, _atomic_json, _read_json_object
from .deployment import DEPLOYMENT_SCHEMA_VERSION, MANAGER_DEFAULT_PORT, MANAGER_VERSION
from .models import ManagerOperation, utc_now
from .release import (
    MAX_LINUX_BUNDLE_BYTES,
    ReleaseDownloadError,
    ReleaseManager,
)
from .service import MaintenanceReservation, ManagerService

UPGRADE_JOURNAL_KIND = "upgrade"
CONFIRMATION_FORMAT = 1
LINUX_PACKAGE_FORMAT = 1
MAX_INNER_MANIFEST_BYTES = 1024 * 1024
MAX_CHECKSUMS_BYTES = 2 * 1024 * 1024
MAX_LINUX_IMAGE_ARCHIVE_BYTES = 15 * 1024**3
MAX_LINUX_TOTAL_UNCOMPRESSED_BYTES = 16 * 1024**3
MAX_LINUX_MEMBER_COUNT = 10_000
LINUX_STAGE_RESERVE_BYTES = 256 * 1024**2
CONFIRMATION_TTL = timedelta(minutes=15)
_GUARD_CACHE_DIGEST_RE = re.compile(r"[0-9a-f]{64}")
_TERMINAL = {"succeeded", "failed", "rejected", "interrupted"}


class UpgradeError(RuntimeError):
    """An upgrade cannot start or failed during a controlled phase."""

    def __init__(self, message: str, *, code: str = "upgrade_failed") -> None:
        self.code = code
        super().__init__(message)


class UpgradeConfirmationError(UpgradeError):
    pass


class UpgradeCompatibilityError(UpgradeError):
    pass


class UpgradeTransactionError(UpgradeError):
    def __init__(self, message: str, *, detail: dict[str, Any]) -> None:
        self.detail = detail
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class VerifiedUpgradePackage:
    version: str
    platform: str
    arch: str
    path: Path
    metadata_path: Path
    artifact: dict[str, Any]
    release: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "platform": self.platform,
            "arch": self.arch,
            "path": str(self.path),
            "artifact": dict(self.artifact),
            "change_scope": list(self.release.get("change_scope", [])),
        }


class UpgradePlatformAdapter(Protocol):
    platform: str

    async def preflight(self, package: VerifiedUpgradePackage) -> dict[str, Any]: ...

    async def capture_current(
        self, package: VerifiedUpgradePackage
    ) -> dict[str, Any]: ...

    async def stage(
        self, package: VerifiedUpgradePackage, transaction_id: str
    ) -> dict[str, Any]: ...

    async def switch(
        self,
        package: VerifiedUpgradePackage,
        *,
        current: dict[str, Any],
        staged: dict[str, Any],
        transaction_id: str,
    ) -> dict[str, Any]: ...

    async def rollback(
        self,
        package: VerifiedUpgradePackage,
        *,
        current: dict[str, Any],
        staged: dict[str, Any],
        transaction_id: str,
    ) -> dict[str, Any]: ...

    async def commit(
        self,
        package: VerifiedUpgradePackage,
        *,
        current: dict[str, Any],
        staged: dict[str, Any],
        transaction_id: str,
    ) -> dict[str, Any]: ...

    def stable_guard_digest(self) -> str | None: ...

    def prune_external_guard_cache(self, keep_digest: str) -> list[str]: ...


class UnsupportedUpgradeAdapter:
    supported = False
    def __init__(self, platform: str, reason: str) -> None:
        self.platform = platform
        self.reason = reason

    async def preflight(self, _package) -> dict[str, Any]:
        raise UpgradeCompatibilityError(self.reason, code="platform_adapter_unavailable")

    async def capture_current(self, _package) -> dict[str, Any]:
        raise UpgradeCompatibilityError(self.reason)

    async def stage(self, _package, _transaction_id) -> dict[str, Any]:
        raise UpgradeCompatibilityError(self.reason)

    async def switch(self, _package, **_kwargs) -> dict[str, Any]:
        raise UpgradeCompatibilityError(self.reason)

    async def rollback(self, _package, **_kwargs) -> dict[str, Any]:
        raise UpgradeCompatibilityError(self.reason)

    async def commit(self, _package, **_kwargs) -> dict[str, Any]:
        raise UpgradeCompatibilityError(self.reason)

    def stable_guard_digest(self) -> str | None:
        return None

    def prune_external_guard_cache(self, keep_digest: str) -> list[str]:
        return []


class LinuxUpgradeExecutor(Protocol):
    """Fixed-operation Docker boundary used after the bundle is verified."""

    async def capture_images(
        self, image_records: list[dict[str, str]]
    ) -> dict[str, Any]: ...

    async def load_images(self, archive: Path) -> dict[str, Any]: ...

    async def resolve_images(
        self, image_records: list[dict[str, str]]
    ) -> dict[str, dict[str, Any]]: ...

    async def switch_images(
        self,
        *,
        target_images: dict[str, dict[str, Any]],
        previous: dict[str, Any],
    ) -> dict[str, Any]: ...

    async def restore_images(self, previous: dict[str, Any]) -> dict[str, Any]: ...


class LinuxBundleUpgradeAdapter:
    """Verify the two-layer Linux contract and delegate fixed Docker actions."""

    platform = "linux"
    supported = True

    def __init__(
        self,
        *,
        layout: InstanceLayout,
        executor: LinuxUpgradeExecutor,
        current_compose: Path | None = None,
    ) -> None:
        self.layout = layout
        self.executor = executor
        self.current_compose = current_compose

    async def preflight(self, package: VerifiedUpgradePackage) -> dict[str, Any]:
        manifest = await asyncio.to_thread(self._validate_bundle, package)
        return {
            "status": "ok",
            "inner_manifest": manifest,
            "network": "not_used",
            "ghcr_fallback": list(
                package.release.get("fallbacks", {}).get("linux_ghcr_images", [])
            ),
        }

    async def capture_current(
        self, package: VerifiedUpgradePackage
    ) -> dict[str, Any]:
        manifest = await asyncio.to_thread(self._validate_bundle, package)
        return await self.executor.capture_images(list(manifest["images"]))

    async def stage(
        self, package: VerifiedUpgradePackage, transaction_id: str
    ) -> dict[str, Any]:
        manifest = await asyncio.to_thread(self._validate_bundle, package)
        if not re.fullmatch(r"[0-9a-f]{32}", transaction_id):
            raise UpgradeCompatibilityError("Upgrade transaction identity is invalid")
        stage_root = self.layout.manager_state_dir / "upgrade-staging"
        stage_dir = stage_root / transaction_id
        await asyncio.to_thread(self._cleanup_orphan_staging, transaction_id)
        image_record = manifest["image_archive"]
        free = shutil.disk_usage(stage_root.parent).free
        required = image_record["size"] + LINUX_STAGE_RESERVE_BYTES
        if free < required:
            raise UpgradeCompatibilityError(
                "Insufficient disk space to stage the Linux image archive",
                code="upgrade_stage_space_insufficient",
            )
        try:
            stage_dir.mkdir(parents=True, exist_ok=False)
            with zipfile.ZipFile(package.path, "r") as archive:
                image_path = _safe_extract_member(
                    archive, image_record["path"], stage_dir
                )
            loaded = await self.executor.load_images(image_path)
            resolved = await self.executor.resolve_images(
                list(manifest["images"])
            )
            return {
                "stage_dir": str(stage_dir),
                "image_archive": str(image_path),
                "images": resolved,
                "loaded": loaded,
                "manifest": manifest,
            }
        except Exception:
            await asyncio.to_thread(self._cleanup_stage_dir, stage_dir)
            raise

    async def switch(
        self,
        package: VerifiedUpgradePackage,
        *,
        current: dict[str, Any],
        staged: dict[str, Any],
        transaction_id: str,
    ) -> dict[str, Any]:
        del package, transaction_id
        return await self.executor.switch_images(
            target_images=dict(staged["images"]),
            previous=current,
        )

    async def rollback(
        self,
        package: VerifiedUpgradePackage,
        *,
        current: dict[str, Any],
        staged: dict[str, Any],
        transaction_id: str,
    ) -> dict[str, Any]:
        del package, transaction_id
        try:
            return await self.executor.restore_images(current)
        finally:
            await asyncio.to_thread(self._cleanup_staged, staged)

    async def commit(
        self,
        package: VerifiedUpgradePackage,
        *,
        current: dict[str, Any],
        staged: dict[str, Any],
        transaction_id: str,
    ) -> dict[str, Any]:
        del package, current, transaction_id
        await asyncio.to_thread(self._cleanup_staged, staged)
        return {"status": "committed", "old_images_retained": True}

    async def cleanup(self, staged: dict[str, Any]) -> None:
        await asyncio.to_thread(self._cleanup_staged, staged)

    def stable_guard_digest(self) -> str | None:
        return None

    def prune_external_guard_cache(self, keep_digest: str) -> list[str]:
        return []

    def _cleanup_staged(self, staged: dict[str, Any]) -> None:
        raw = staged.get("stage_dir")
        if not isinstance(raw, str) or not raw:
            return
        self._cleanup_stage_dir(Path(raw))

    def _cleanup_orphan_staging(self, active_transaction_id: str) -> None:
        root = self.layout.manager_state_dir / "upgrade-staging"
        root.mkdir(parents=True, exist_ok=True)
        for child in root.iterdir():
            if child.name != active_transaction_id:
                self._cleanup_stage_dir(child)

    def _cleanup_stage_dir(self, path: Path) -> None:
        root = self.layout.manager_state_dir / "upgrade-staging"
        if (
            path.parent != root
            or not re.fullmatch(r"[0-9a-f]{32}", path.name)
        ):
            raise UpgradeCompatibilityError(
                "Refusing to clean an untrusted upgrade staging path"
            )
        if path.is_symlink():
            path.unlink(missing_ok=True)
        elif path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()

    def _validate_bundle(
        self, package: VerifiedUpgradePackage
    ) -> dict[str, Any]:
        if package.platform != "linux":
            raise UpgradeCompatibilityError("Linux adapter received another platform")
        try:
            package_size = package.path.stat().st_size
        except OSError as exc:
            raise UpgradeCompatibilityError(
                f"Linux release bundle is unavailable: {exc}"
            ) from exc
        if (
            package_size > MAX_LINUX_BUNDLE_BYTES
            or package.artifact.get("size") != package_size
        ):
            raise UpgradeCompatibilityError(
                "Linux release bundle size is invalid or exceeds the limit"
            )
        try:
            with zipfile.ZipFile(package.path, "r") as archive:
                _validate_zip_members(archive)
                manifest = _read_zip_json(
                    archive,
                    "dicepp-package.json",
                    MAX_INNER_MANIFEST_BYTES,
                )
                checksums_raw = _read_zip_member(
                    archive, "checksums.sha256", MAX_CHECKSUMS_BYTES
                )
                checksums = _parse_checksums(checksums_raw)
                _validate_linux_manifest(manifest, package)
                required_records = (manifest["compose"], manifest["image_archive"])
                for record in required_records:
                    _verify_zip_record(archive, record)
                for name, digest in checksums.items():
                    if name in {"checksums.sha256"}:
                        continue
                    actual_digest = hashlib.sha256()
                    with archive.open(name, "r") as checksum_source:
                        for chunk in iter(
                            lambda: checksum_source.read(1024 * 1024), b""
                        ):
                            actual_digest.update(chunk)
                    actual = actual_digest.hexdigest()
                    if actual != digest:
                        raise UpgradeCompatibilityError(
                            f"Linux bundle checksum differs: {name}"
                        )
                for required in (
                    "dicepp-package.json",
                    manifest["compose"]["path"],
                    manifest["image_archive"]["path"],
                ):
                    if required not in checksums:
                        raise UpgradeCompatibilityError(
                            f"checksums.sha256 does not cover {required}"
                        )
                if self.current_compose is not None:
                    current = _compose_topology(
                        self.current_compose.read_text(encoding="utf-8")
                    )
                    target = _compose_topology(
                        archive.read(manifest["compose"]["path"]).decode("utf-8")
                    )
                    if current != target:
                        raise UpgradeCompatibilityError(
                            "Release changes Compose service/volume/network topology; "
                            "manual deployment migration is required",
                            code="compose_topology_changed",
                        )
                elif self.current_compose is None:
                    raise UpgradeCompatibilityError(
                        "Current Compose file is unavailable; automatic upgrade "
                        "cannot prove topology compatibility",
                        code="compose_topology_unavailable",
                    )
        except (OSError, zipfile.BadZipFile, KeyError, UnicodeDecodeError) as exc:
            raise UpgradeCompatibilityError(
                f"Linux release bundle is invalid: {exc}"
            ) from exc
        return manifest


class WindowsVelopackUpgradeAdapter:
    """Create a durable UpdateGuard request outside versioned program dirs."""

    platform = "windows"
    supported = True

    def __init__(
        self,
        *,
        layout: InstanceLayout,
        guard_command: list[str],
        install_command: list[str],
        process_identity_loader: Callable[[], dict[str, Any]],
        version_loader: Callable[[], str] = get_version,
        bundled_guard_path: Path | None = None,
        restart_command: list[str] | None = None,
        health_url: str = f"http://127.0.0.1:{MANAGER_DEFAULT_PORT}/v1/health",
        auth_token_path: Path | None = None,
        manager_exit_timeout: float = 60.0,
        health_timeout: float = 120.0,
        rollback_package_fetcher: Callable[[str], tuple[Path, str]] | None = None,
        guard_runtime_root: Path | None = None,
    ) -> None:
        if not guard_command or not install_command:
            raise ValueError("UpdateGuard and Velopack commands are required")
        self.layout = layout
        self.guard_command = list(guard_command)
        self.install_command = list(install_command)
        self.restart_command = list(
            restart_command or [str(layout.root / "DicePP.exe")]
        )
        self.process_identity_loader = process_identity_loader
        self.version_loader = version_loader
        self.rollback_package_fetcher = rollback_package_fetcher
        self._fetched_rollback: dict[str, tuple[Path, str]] = {}
        self.bundled_guard_path = bundled_guard_path or (
            Path(os.environ.get("DICEPP_APP_DIR", str(layout.root)))
            / "DicePP-UpdateGuard.exe"
        )
        self.health_url = health_url
        self.auth_token_path = auth_token_path or layout.manager_token
        self.manager_exit_timeout = manager_exit_timeout
        self.health_timeout = health_timeout
        self.guard_dir = layout.manager_state_dir / "update-guard"
        default_guard_runtime_root = (
            Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir()))
            / "DicePP-UpdateGuard"
        )
        instance_key = hashlib.sha256(
            os.path.normcase(str(layout.root.resolve())).encode("utf-8")
        ).hexdigest()[:16]
        self.guard_runtime_dir = (
            Path(guard_runtime_root or default_guard_runtime_root).resolve()
            / instance_key
        )
        if self.guard_runtime_dir.is_relative_to(layout.root.resolve()):
            raise ValueError(
                "UpdateGuard runtime directory must be outside the install root"
            )

    async def preflight(self, package: VerifiedUpgradePackage) -> dict[str, Any]:
        if package.platform != "windows":
            raise UpgradeCompatibilityError("Windows adapter received another platform")
        identity = _validate_process_identity(self.process_identity_loader())
        if package.artifact.get("purpose") != "velopack-full":
            raise UpgradeCompatibilityError(
                "Windows automatic updates require a Velopack full package; "
                "Portable and Setup are first-install artifacts"
            )
        if Version(self._current_version()) == Version(package.version):
            raise UpgradeCompatibilityError(
                "Windows automatic update target is already installed"
            )
        source_version, rollback_package, rollback_digest = (
            await self._current_full_package()
        )
        update_exe = Path(self.install_command[0])
        stable_guard = Path(self.guard_command[0])
        bundled_guard = self.bundled_guard_path
        current_dir = self.layout.root / "current"
        restart_exe = Path(self.restart_command[0])
        if (
            not update_exe.is_absolute()
            or update_exe.is_symlink()
            or not update_exe.is_file()
            or current_dir.is_symlink()
            or not current_dir.is_dir()
            or not restart_exe.is_absolute()
            or restart_exe.is_symlink()
            or not restart_exe.is_file()
            or not stable_guard.is_absolute()
            or stable_guard.is_symlink()
            or not stable_guard.is_file()
            or bundled_guard.is_symlink()
            or not bundled_guard.is_file()
        ):
            raise UpgradeCompatibilityError(
                "Velopack stable root is incomplete (Update.exe/current/root launcher)"
            )
        if _sha256_file(stable_guard) != _sha256_file(bundled_guard):
            raise UpgradeCompatibilityError(
                "Stable UpdateGuard does not match the current version; "
                "wait for Guard refresh or use a manual update"
            )
        return {
            "status": "ok",
            "process_identity": identity,
            "source_version": source_version,
            "rollback_package": str(rollback_package),
            "rollback_package_sha256": rollback_digest,
        }

    async def capture_current(self, package) -> dict[str, Any]:
        if Version(self._current_version()) == Version(package.version):
            raise UpgradeCompatibilityError(
                "Windows automatic update target is already installed"
            )
        source_version, rollback_package, rollback_digest = (
            await self._current_full_package()
        )
        return {
            "process_identity": _validate_process_identity(
                self.process_identity_loader()
            ),
            "source_version": source_version,
            "rollback_package": str(rollback_package),
            "rollback_package_sha256": rollback_digest,
        }

    async def stage(
        self, package: VerifiedUpgradePackage, transaction_id: str
    ) -> dict[str, Any]:
        # Resolve the rollback material before creating the transaction
        # directory: the resolution may download from the Release, and a
        # failure there must not leave an orphan guard_dir/<uuid>/ behind.
        source_version, rollback_source, rollback_digest = (
            await self._current_full_package()
        )
        transaction_dir = self.guard_dir / transaction_id
        transaction_dir.mkdir(parents=True, exist_ok=False)
        rollback_package = transaction_dir / rollback_source.name
        _atomic_copy(rollback_source, rollback_package)
        if _sha256_file(rollback_package) != rollback_digest:
            raise UpgradeCompatibilityError(
                "Preserved rollback package digest mismatch"
            )
        return {
            "package": str(package.path),
            "request": str(transaction_dir / "request.json"),
            "guard_marker": str(transaction_dir / "guard.json"),
            "started_marker": str(transaction_dir / "started.json"),
            "health_marker": str(transaction_dir / "health.json"),
            "rollback_marker": str(transaction_dir / "rollback.json"),
            "source_version": source_version,
            "rollback_package": str(rollback_package),
            "rollback_package_sha256": rollback_digest,
            "transaction_id": transaction_id,
        }

    async def switch(
        self,
        package: VerifiedUpgradePackage,
        *,
        current: dict[str, Any],
        staged: dict[str, Any],
        transaction_id: str,
    ) -> dict[str, Any]:
        request = {
            "format_version": 2,
            "transaction_id": transaction_id,
            "target_version": package.version,
            "source_version": current["source_version"],
            "package": str(package.path),
            "package_sha256": package.artifact["sha256"],
            "rollback_package": staged["rollback_package"],
            "rollback_package_sha256": staged["rollback_package_sha256"],
            "manager_identity": current["process_identity"],
            "guard_marker": staged["guard_marker"],
            "started_marker": staged["started_marker"],
            "health_marker": staged["health_marker"],
            "rollback_marker": staged["rollback_marker"],
            "health_url": self.health_url,
            "auth_token_path": str(self.auth_token_path.resolve()),
            "install_command": [
                value.replace("{package}", str(package.path)).replace(
                    "{package_dir}", str(package.path.parent)
                )
                for value in self.install_command
            ],
            "rollback_command": [
                value.replace("{package}", staged["rollback_package"]).replace(
                    "{package_dir}", str(Path(staged["rollback_package"]).parent)
                )
                for value in self.install_command
            ],
            "restart_command": list(self.restart_command),
            "manager_exit_timeout_seconds": self.manager_exit_timeout,
            "health_timeout_seconds": self.health_timeout,
            "requested_at": utc_now(),
        }
        _atomic_json(Path(staged["request"]), request)
        process, guard_executable = self.start_guard(
            Path(staged["request"])
        )
        return {
            "guard_pid": process.pid,
            "guard_executable": str(guard_executable),
            "handoff_required": True,
            "request": staged["request"],
        }

    def start_guard(
        self, request_path: Path
    ) -> tuple[subprocess.Popen, Path]:
        """Start an independent Guard from outside the Velopack install root."""

        guard_executable = self._prepare_external_guard()
        guard_output = request_path.parent / "guard-output.log"
        guard_environment = os.environ.copy()
        guard_environment["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
        with guard_output.open("ab", buffering=0) as output:
            process = subprocess.Popen(
                [
                    str(guard_executable),
                    *self.guard_command[1:],
                    "--request",
                    str(request_path),
                ],
                env=guard_environment,
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=subprocess.STDOUT,
            )
        return process, guard_executable

    def _prepare_external_guard(self) -> Path:
        source = Path(self.guard_command[0])
        if (
            not source.is_absolute()
            or source.is_symlink()
            or not source.is_file()
        ):
            raise UpgradeCompatibilityError(
                "Stable UpdateGuard executable is unavailable"
            )
        source_digest = _sha256_file(source)
        version_dir = self.guard_runtime_dir / source_digest
        version_dir.mkdir(parents=True, exist_ok=True)
        resolved_version_dir = version_dir.resolve(strict=True)
        if (
            version_dir.is_symlink()
            or resolved_version_dir.is_relative_to(self.layout.root.resolve())
        ):
            raise UpgradeCompatibilityError(
                "UpdateGuard runtime directory is unsafe"
            )
        target = resolved_version_dir / "DicePP-UpdateGuard.exe"
        _atomic_copy(source, target)
        if (
            target.is_symlink()
            or not target.is_file()
            or _sha256_file(target) != source_digest
        ):
            raise UpgradeCompatibilityError(
                "External UpdateGuard executable digest mismatch"
            )
        return target

    def stable_guard_digest(self) -> str | None:
        """SHA-256 of the stable-root Guard, i.e. its runtime cache dir name."""
        return _sha256_file(Path(self.guard_command[0]))

    def prune_external_guard_cache(self, keep_digest: str) -> list[str]:
        """Delete stale external Guard versions, keeping ``keep_digest``.

        Only direct children of ``guard_runtime_dir`` are touched: symlinks
        and Windows junctions are removed without following, and a real
        directory is removed recursively only after its resolved path is
        confirmed to stay inside ``guard_runtime_dir`` (a TOCTOU window
        between the check and the removal remains, which is acceptable for
        a per-user LOCALAPPDATA cache).  Returns the removed child names
        for audit.
        """
        if not _GUARD_CACHE_DIGEST_RE.fullmatch(keep_digest):
            raise ValueError(
                "UpdateGuard cache keep digest must be a SHA-256 hex string"
            )
        runtime_dir = self.guard_runtime_dir
        if not runtime_dir.is_dir():
            return []
        removed: list[str] = []
        for child in runtime_dir.iterdir():
            if child.name == keep_digest:
                continue
            if child.is_junction():
                # A junction is not a symlink; remove only the reparse
                # point itself, never the target's contents.
                os.rmdir(child)
            elif child.is_symlink() or not child.is_dir():
                child.unlink()
            else:
                if not child.resolve().is_relative_to(runtime_dir):
                    continue
                shutil.rmtree(child)
            removed.append(child.name)
        return removed

    def validate_rollback_marker(
        self, detail: dict[str, Any]
    ) -> dict[str, Any] | None:
        staged = detail.get("platform_staged")
        if not isinstance(staged, dict):
            return None
        marker_path = Path(str(staged.get("rollback_marker", "")))
        if (
            not marker_path.is_absolute()
            or marker_path.is_symlink()
            or not marker_path.is_file()
        ):
            return None
        marker = _read_json_object(marker_path)
        expected_identity = marker.get("manager_identity")
        try:
            expected_identity = _validate_process_identity(expected_identity)
        except UpgradeCompatibilityError:
            return None
        if (
            marker.get("format_version") != 2
            or marker.get("status")
            not in {
                "program_rollback_started",
                "program_rolled_back",
                "program_rollback_failed",
            }
            or marker.get("transaction_id") != detail.get("transaction_id")
            or marker.get("target_version") != detail.get("target_version")
            or marker.get("source_version") != staged.get("source_version")
            or not _identity_belongs_to_instance(
                expected_identity, self.layout.root
            )
        ):
            return None
        return marker

    def _current_version(self) -> str:
        current_version = self.version_loader()
        if not isinstance(current_version, str) or current_version == "unknown":
            raise UpgradeCompatibilityError(
                "Current Windows program version is unavailable"
            )
        return current_version

    def _local_full_packages(self, current_version: str) -> list[Path]:
        packages_dir = self.layout.root / "packages"
        matches: list[Path] = []
        if packages_dir.is_dir() and not packages_dir.is_symlink():
            for candidate in packages_dir.glob("*-full.nupkg"):
                if (
                    candidate.is_file()
                    and not candidate.is_symlink()
                    and _nupkg_version(candidate) == current_version
                ):
                    matches.append(candidate.resolve())
        return matches

    async def _current_full_package(self) -> tuple[str, Path, str]:
        current_version = self._current_version()
        matches = self._local_full_packages(current_version)
        if len(matches) > 1:
            raise UpgradeCompatibilityError(
                "Exactly one verified current-version Velopack full package "
                "is required for automatic rollback"
            )
        if len(matches) == 1:
            return current_version, matches[0], _sha256_file(matches[0])
        if self.rollback_package_fetcher is None:
            raise UpgradeCompatibilityError(
                "Exactly one verified current-version Velopack full package "
                "is required for automatic rollback"
            )
        # Neither the Portable zip nor Update.exe maintains root/packages,
        # so the current-version full package may be missing locally.  Fetch
        # it from the GitHub Release (verified against the Release contract)
        # instead of refusing the automatic update.  The result is memoized:
        # preflight, capture_current, and stage all resolve the material.
        cached = self._fetched_rollback.get(current_version)
        if cached is not None and cached[0].is_file():
            return current_version, cached[0], cached[1]
        try:
            path, digest = await asyncio.to_thread(
                self.rollback_package_fetcher, current_version
            )
        except Exception as exc:
            raise UpgradeCompatibilityError(
                "The current-version Velopack full package is unavailable "
                "locally and could not be fetched from the Release; "
                "use a manual Windows update"
            ) from exc
        self._fetched_rollback[current_version] = (path, digest)
        return current_version, path, digest

    def _maintain_packages_dir(self, package: VerifiedUpgradePackage) -> str | None:
        """Best-effort: keep exactly the committed full package in root/packages.

        Neither Update.exe apply nor DicePP maintains the Velopack packages
        directory; without this housekeeping the next automatic update would
        not find its rollback material locally.  A failure here must not
        invalidate an already healthy commit, so it is reported, not raised.
        """
        try:
            packages_dir = self.layout.root / "packages"
            if packages_dir.is_symlink():
                return "Velopack packages directory is not a regular directory"
            packages_dir.mkdir(parents=True, exist_ok=True)
            expected = package.artifact.get("sha256")
            if not isinstance(expected, str) or not expected:
                return "Committed package digest is unavailable"
            target = packages_dir / package.path.name
            if (
                target.is_symlink()
                or not target.is_file()
                or _sha256_file(target) != expected
            ):
                _atomic_copy(package.path, target)
            if _sha256_file(target) != expected:
                return "Refreshed rollback package digest mismatch"
            for candidate in packages_dir.glob("*-full.nupkg"):
                if (
                    candidate.is_file()
                    and candidate.resolve() != target.resolve()
                ):
                    candidate.unlink()
            return None
        except OSError as exc:
            return str(exc) or type(exc).__name__

    async def rollback(
        self,
        package: VerifiedUpgradePackage,
        *,
        current: dict[str, Any],
        staged: dict[str, Any],
        transaction_id: str,
    ) -> dict[str, Any]:
        del current
        marker = Path(staged["rollback_marker"])
        if not marker.is_file():
            raise UpgradeError(
                "UpdateGuard has not confirmed program rollback",
                code="guard_rollback_pending",
            )
        result = _read_json_object(marker)
        if (
            result.get("format_version") != 2
            or result.get("transaction_id") != transaction_id
            or result.get("target_version") != package.version
            or result.get("source_version") != staged.get("source_version")
            or result.get("status") != "program_rolled_back"
        ):
            raise UpgradeError("UpdateGuard rollback marker protocol mismatch")
        return result

    async def commit(
        self,
        package: VerifiedUpgradePackage,
        *,
        current: dict[str, Any],
        staged: dict[str, Any],
        transaction_id: str,
    ) -> dict[str, Any]:
        del current
        marker = Path(staged["health_marker"])
        if not marker.is_file():
            raise UpgradeError(
                "UpdateGuard health marker is missing",
                code="guard_health_pending",
            )
        result = _read_json_object(marker)
        if (
            result.get("format_version") != 2
            or result.get("transaction_id") != transaction_id
            or result.get("target_version") != package.version
            or result.get("status") != "healthy"
            or not _identity_belongs_to_instance(
                _validate_process_identity(result.get("manager_identity")),
                self.layout.root,
            )
        ):
            raise UpgradeError("UpdateGuard did not report healthy")
        maintenance_error = await asyncio.to_thread(
            self._maintain_packages_dir, package
        )
        if maintenance_error is not None:
            result = {**result, "packages_maintenance_error": maintenance_error}
        return result

    def validate_health_marker(
        self, detail: dict[str, Any]
    ) -> dict[str, Any] | None:
        staged = detail.get("platform_staged")
        if not isinstance(staged, dict):
            return None
        path = Path(str(staged.get("health_marker", "")))
        if not path.is_absolute() or path.is_symlink() or not path.is_file():
            return None
        try:
            marker = _read_json_object(path)
            identity = _validate_process_identity(marker.get("manager_identity"))
        except (OSError, ValueError, UpgradeCompatibilityError):
            return None
        if (
            marker.get("format_version") != 2
            or marker.get("transaction_id") != detail.get("transaction_id")
            or marker.get("target_version") != detail.get("target_version")
            or marker.get("status") not in {"healthy", "failed"}
            or not _identity_belongs_to_instance(identity, self.layout.root)
        ):
            return None
        return marker


class UpgradeCoordinator:
    def __init__(
        self,
        *,
        layout: InstanceLayout,
        service: ManagerService,
        archive_coordinator: ArchiveCoordinator,
        release_manager: ReleaseManager,
        platform_adapter: UpgradePlatformAdapter,
        now: Callable[[], datetime] | None = None,
        fault_hook: Callable[[str], None] | None = None,
    ) -> None:
        self.layout = layout
        self.service = service
        self.store = service.store
        self.archive = archive_coordinator
        self.release_manager = release_manager
        self.platform_adapter = platform_adapter
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.fault_hook = fault_hook
        self._api_ready = asyncio.Event()
        self._handoff_status: dict[str, Any] | None = None
        self.confirmation_path = layout.manager_state_dir / "upgrade-confirmation.json"

    @property
    def install_supported(self) -> bool:
        return bool(getattr(self.platform_adapter, "supported", True))

    def new_operation(self) -> ManagerOperation:
        operation = ManagerOperation.create_system("upgrade.install")
        self.store.save(operation)
        return operation

    @contextmanager
    def _maintenance_context(
        self,
        reservation: MaintenanceReservation | None,
        *,
        timeout: float = 0,
        allow_startup_recovery: bool = False,
    ):
        if reservation is not None:
            # A transferred HTTP reservation remains owned by the critical
            # task until its durable operation and journal terminal state are
            # saved.  Do not release it when an inner coordinator scope ends.
            yield reservation.session
            return
        with self.service.maintenance(
            timeout=timeout,
            allow_startup_recovery=allow_startup_recovery,
        ) as maintenance:
            yield maintenance

    def status(self) -> dict[str, Any]:
        recent = [
            op
            for op in self.store.list_recent(100)
            if op.action.startswith("upgrade.")
        ]
        active = next((op for op in recent if op.status not in _TERMINAL), None)
        last = recent[0] if recent else None
        journal = None
        if active is not None:
            transaction_id = active.detail.get("transaction_id")
            if isinstance(transaction_id, str):
                journal = self.store.get_journal(transaction_id)
        return {
            "active_operation": active.to_dict() if active else None,
            "last_operation": last.to_dict() if last else None,
            "journal": journal,
        }

    async def preview(self, version: str | None = None) -> dict[str, Any]:
        package = self._verified_package(version)
        platform = await self.platform_adapter.preflight(package)
        estimate = estimate_archive(self.layout, "regular")
        if not estimate["enough_space"]:
            raise UpgradeCompatibilityError(
                "Insufficient disk space for the pre-upgrade archive"
            )
        token = secrets.token_urlsafe(32)
        expires = self.now() + CONFIRMATION_TTL
        payload = {
            "format_version": CONFIRMATION_FORMAT,
            "token_hash": hashlib.sha256(token.encode()).hexdigest(),
            "version": package.version,
            "artifact_sha256": package.artifact["sha256"],
            "expires_at": expires.isoformat(),
        }
        _atomic_json(self.confirmation_path, payload)
        return {
            "version": package.version,
            "platform": package.platform,
            "artifact": package.artifact["filename"],
            "release_snapshot": package.release,
            "change_scope": list(package.release.get("change_scope", [])),
            "downtime_required": True,
            "pre_upgrade_archive": "regular",
            "automatic_rollback": True,
            "estimated_archive_bytes": estimate["input_bytes"],
            "warnings": [
                "External NapCat/QQ/GitHub/LLM availability is warning-only"
            ],
            "platform_preflight": platform,
            "confirmation_token": token,
            "expires_at": expires.isoformat(),
        }

    def confirm(
        self, *, version: str, confirmation_token: str
    ) -> tuple[ManagerOperation, VerifiedUpgradePackage]:
        if not isinstance(confirmation_token, str) or len(confirmation_token) < 32:
            raise UpgradeConfirmationError(
                "A valid upgrade confirmation token is required",
                code="confirmation_required",
            )
        package = self._verified_package(version)
        try:
            confirmation = _read_json_object(self.confirmation_path)
            expires = datetime.fromisoformat(
                str(confirmation["expires_at"]).replace("Z", "+00:00")
            )
        except (OSError, ValueError, KeyError) as exc:
            raise UpgradeConfirmationError(
                "Upgrade preview is missing or expired",
                code="confirmation_missing",
            ) from exc
        supplied_hash = hashlib.sha256(confirmation_token.encode()).hexdigest()
        valid = (
            confirmation.get("format_version") == CONFIRMATION_FORMAT
            and secrets.compare_digest(
                str(confirmation.get("token_hash", "")), supplied_hash
            )
            and confirmation.get("version") == package.version
            and confirmation.get("artifact_sha256") == package.artifact["sha256"]
            and expires > self.now()
        )
        if not valid:
            raise UpgradeConfirmationError(
                "Upgrade confirmation does not match the current verified candidate",
                code="confirmation_mismatch",
            )
        self.confirmation_path.unlink(missing_ok=True)
        operation = self.new_operation()
        operation.detail = {
            "phase": "queued",
            "progress": 0,
            "target_version": package.version,
        }
        self.store.save(operation)
        return operation, package

    async def run(
        self,
        operation: ManagerOperation,
        package: VerifiedUpgradePackage,
        *,
        maintenance_lease: MaintenanceReservation | None = None,
    ) -> ManagerOperation:
        transaction_id = uuid4().hex
        detail: dict[str, Any] = {
            "transaction_id": transaction_id,
            "target_version": package.version,
            "platform": package.platform,
            "artifact": package.artifact["filename"],
            "release_snapshot": package.release,
            "phase": "preflight",
            "progress": 5,
            "original_running": [],
            "commit_point": "not_started",
            "rolled_back": False,
            "rollback_status": "not_started",
        }
        operation.transition("running", detail=detail)
        self.store.save(operation)
        self._journal(operation, detail)
        try:
            preflight = await self.platform_adapter.preflight(package)
            detail["preflight"] = preflight
            self._phase(operation, detail, "pre_upgrade_archive", 15)
            baseline, control_gate = await self.archive._capture_control_baseline()
            detail["control_heartbeat_baseline"] = baseline
            detail["control_gate"] = control_gate
            with self._maintenance_context(maintenance_lease) as maintenance:
                original, _ = await self.archive._quiesce(
                    maintenance,
                    state_callback=lambda running: self._record_running(
                        operation, detail, running
                    ),
                )
                self._fault("quiesce")
                estimate = estimate_archive(self.layout, "regular")
                if not estimate["enough_space"]:
                    raise UpgradeError("Insufficient disk space for pre-upgrade archive")
                pre, _manifest = await asyncio.to_thread(
                    create_archive,
                    f"pre-upgrade {package.version}",
                    layout=self.layout,
                    profile="regular",
                    archive_kind="system",
                )
                detail["pre_upgrade_filename"] = pre["filename"]
                self._phase(operation, detail, "program_stage", 30)
                self._fault("pre_upgrade_archive")
                current = await self.platform_adapter.capture_current(package)
                staged = await self.platform_adapter.stage(package, transaction_id)
                detail["platform_current"] = current
                detail["platform_staged"] = staged
                self._phase(operation, detail, "program_switch", 50)
                detail["commit_point"] = "program_switch_started"
                self._journal(operation, detail)
                switch = await self.platform_adapter.switch(
                    package,
                    current=current,
                    staged=staged,
                    transaction_id=transaction_id,
                )
                detail["program_switch"] = switch
                self._fault("program_switch")
                if switch.get("handoff_required") is True:
                    detail["phase"] = "awaiting_update_guard"
                    detail["progress"] = 55
                    self._journal(operation, detail, phase="awaiting_update_guard")
                    operation.transition(
                        "running",
                        message="UpdateGuard is waiting for Manager hand-off",
                        detail=detail,
                    )
                    self.store.save(operation)
                    asyncio.get_running_loop().call_later(
                        0.25,
                        self.service.request_shutdown,
                        "windows_update_guard_handoff",
                    )
                    return operation
                self._phase(operation, detail, "migration", 65)
                self._fault("migration")
                migrations = await asyncio.to_thread(
                    self.archive._migrate_and_validate_schema
                )
                detail["migrations"] = migrations
                self._phase(operation, detail, "runtime_start", 75)
                await self.archive._restart(maintenance, original)
                self._fault("runtime_start")
                self._phase(operation, detail, "health", 85)
                health = await self.archive._hard_health(
                    original,
                    control_baseline=detail.get("control_heartbeat_baseline"),
                    control_gate=control_gate,
                )
                self._fault("health")
                detail["health"] = health
                detail["commit_point"] = "health_passed"
                self._journal(operation, detail, phase="healthy")
                commit = await self.platform_adapter.commit(
                    package,
                    current=current,
                    staged=staged,
                    transaction_id=transaction_id,
                )
                detail["platform_commit"] = commit
                detail["phase"] = "committed"
                detail["progress"] = 100
                self._journal(operation, detail, phase="committed", status="committed")
            self.archive._apply_retention_if_safe()
            operation.transition(
                "succeeded",
                message=f"Upgrade to {package.version} committed",
                detail=detail,
            )
            self.store.save(operation)
            self.store.retire_terminal_rollback_journals()
            self._prune_external_guard_cache(operation, detail)
            return operation
        except Exception as exc:
            rollback = await self._rollback(
                operation,
                package,
                detail,
                maintenance_lease=maintenance_lease,
            )
            failed = {
                **detail,
                "phase": "failed",
                "error": str(exc) or type(exc).__name__,
                "failure_code": getattr(exc, "code", "upgrade_failed"),
                "rollback_result": rollback,
                "rolled_back": rollback.get("succeeded", False),
                "rollback_status": (
                    "succeeded" if rollback.get("succeeded") else "failed"
                ),
            }
            operation.transition("failed", message=failed["error"], detail=failed)
            self.store.save(operation)
            raise UpgradeTransactionError(failed["error"], detail=failed) from exc

    async def recover(
        self,
        *,
        prepare_windows_handoff_only: bool = False,
        allow_startup_recovery: bool = False,
    ) -> list[dict[str, Any]]:
        recovered: list[dict[str, Any]] = []
        for journal in self.store.list_recoverable_journals():
            if journal.get("kind") != UPGRADE_JOURNAL_KIND:
                continue
            detail = dict(journal.get("detail") or {})
            transaction_id = str(journal["transaction_id"])
            rollback_validator = getattr(
                self.platform_adapter,
                "validate_rollback_marker",
                None,
            )
            health_validator = getattr(
                self.platform_adapter,
                "validate_health_marker",
                None,
            )
            authoritative_rollback = None
            authoritative_health = None
            if callable(rollback_validator):
                try:
                    authoritative_rollback = rollback_validator(detail)
                except (OSError, ValueError, json.JSONDecodeError):
                    # A malformed/unreadable marker is never authoritative.
                    authoritative_rollback = None
            if callable(health_validator):
                try:
                    authoritative_health = health_validator(detail)
                except (OSError, ValueError, json.JSONDecodeError):
                    authoritative_health = None
            # Terminal rollback adjudication rule (shared with
            # archive_coordinator.ArchiveCoordinator.recover): a rollback
            # that already ran its destructive phase and was adjudicated
            # failed is terminal and requires manual recovery.  Replaying it
            # after a restart would only repeat the damage (stop Bots,
            # rebuild the old containers, re-apply the old archive).  A
            # rollback that failed before the program switch only owes a
            # best-effort restart and stays retryable.  Exemption: when the
            # UpdateGuard rollback marker already validated
            # program_rolled_back, the destructive program rollback is known
            # to have completed and only data recovery/restart/health
            # failed; retrying via _recover_update_guard_handoff never
            # replays the destructive phase, so the journal stays
            # recoverable instead of terminal.
            if (
                journal.get("status") == "rollback_failed"
                and detail.get("commit_point") not in (None, "not_started")
                and not (
                    isinstance(authoritative_rollback, dict)
                    and authoritative_rollback.get("status")
                    == "program_rolled_back"
                )
            ):
                recovered.append(
                    {
                        "transaction_id": transaction_id,
                        "action": "rollback_failed",
                        "manual_recovery_required": True,
                    }
                )
                continue
            operation = (
                self.store.get(str(journal.get("operation_id")))
                if journal.get("operation_id")
                else None
            )
            if operation is None:
                operation = self.new_operation()
            guard_handoff = self._is_guard_handoff(
                journal,
                detail,
                rollback_validator=rollback_validator,
                health_validator=health_validator,
            )
            if guard_handoff:
                self.service.set_startup_maintenance_gate(True)
            if (
                isinstance(authoritative_rollback, dict)
                and authoritative_rollback.get("status") == "program_rolled_back"
            ):
                guard_result = await self._recover_update_guard_handoff(
                    operation,
                    None,
                    detail,
                    validated_rollback_marker=authoritative_rollback,
                    allow_startup_recovery=allow_startup_recovery,
                )
                recovered.append(
                    {
                        "transaction_id": transaction_id,
                        **guard_result,
                    }
                )
                continue
            guard_waiting_for_rollback = (
                isinstance(authoritative_rollback, dict)
                and authoritative_rollback.get("status")
                == "program_rollback_started"
            )
            guard_reported_health_failure = (
                isinstance(authoritative_health, dict)
                and authoritative_health.get("status") == "failed"
            )
            guard_health_committed = (
                isinstance(authoritative_health, dict)
                and authoritative_health.get("status") == "healthy"
            )
            guard_pending = (
                guard_handoff
                and not guard_health_committed
                and not (
                    isinstance(authoritative_rollback, dict)
                    and authoritative_rollback.get("status")
                    in {"program_rolled_back", "program_rollback_failed"}
                )
            )
            if guard_pending:
                self._normalize_guard_handoff(
                    operation,
                    detail,
                    marker=authoritative_rollback or authoritative_health,
                )
            preparing_handoff = guard_pending or (
                journal.get("phase") == "awaiting_update_guard"
            )
            if preparing_handoff and prepare_windows_handoff_only:
                self.service.set_startup_maintenance_gate(True)
                try:
                    prepared = self._publish_started_marker(detail)
                except BaseException:
                    self.service.set_startup_maintenance_gate(False)
                    raise
                if guard_waiting_for_rollback or guard_reported_health_failure:
                    self._handoff_status = dict(
                        authoritative_rollback or authoritative_health
                    )
                recovered.append(
                    {
                        "transaction_id": transaction_id,
                        "action": "awaiting_api_bind",
                        "result": prepared,
                    }
                )
                continue
            if guard_pending and (
                guard_waiting_for_rollback or guard_reported_health_failure
            ):
                marker = authoritative_rollback or authoritative_health
                self._handoff_status = dict(marker)
                self.service.request_shutdown(
                    "windows_update_guard_rollback_pending"
                )
                recovered.append(
                    {
                        "transaction_id": transaction_id,
                        "action": "awaiting_guard_rollback",
                        "result": marker,
                    }
                )
                continue
            try:
                release_snapshot = detail.get("release_snapshot")
                if not isinstance(release_snapshot, dict):
                    raise UpgradeCompatibilityError(
                        "Interrupted upgrade has no durable Release snapshot"
                    )
                package = self._package_from_release(
                    str(detail.get("target_version") or ""),
                    release_snapshot,
                )
            except Exception as exc:
                cleanup_error = await self._cleanup_platform_staging(detail)
                operation.transition(
                    "failed",
                    message="Interrupted upgrade package is unavailable",
                    detail={
                        **detail,
                        "recovered": True,
                        "recovery_error": str(exc),
                        "staging_cleanup_error": cleanup_error,
                    },
                )
                self.store.save(operation)
                recovered.append(
                    {"transaction_id": transaction_id, "action": "package_missing"}
                )
                continue
            if detail.get("commit_point") == "health_passed":
                if (
                    not callable(health_validator)
                    or guard_health_committed
                ):
                    detail["platform_commit"] = (
                        await self.platform_adapter.commit(
                            package,
                            current=dict(detail.get("platform_current") or {}),
                            staged=dict(detail.get("platform_staged") or {}),
                            transaction_id=transaction_id,
                        )
                    )
                    self._journal(
                        operation, detail, phase="committed", status="committed"
                    )
                    operation.transition(
                        "succeeded",
                        message="Upgrade commit finalized after Manager restart",
                        detail={**detail, "recovered": True},
                    )
                    self.store.save(operation)
                    self.store.retire_terminal_rollback_journals()
                    self._prune_external_guard_cache(operation, detail)
                    # Commit, journal, and operation state are now durable.
                    # Later Guard refresh/cleanup may retry independently and
                    # must not keep ordinary runtime lifecycle work blocked.
                    self.service.set_startup_maintenance_gate(False)
                    recovered.append(
                        {"transaction_id": transaction_id, "action": "finalized"}
                    )
                    continue
            if guard_pending or journal.get("phase") == "awaiting_update_guard":
                guard_result = await self._recover_update_guard_handoff(
                    operation,
                    package,
                    detail,
                    allow_startup_recovery=allow_startup_recovery,
                )
                recovered.append(
                    {
                        "transaction_id": transaction_id,
                        **guard_result,
                    }
                )
                continue
            if detail.get("commit_point") == "not_started":
                self.archive._cleanup_inprogress()
                cleanup_error = await self._cleanup_platform_staging(detail)
                restart_error = await self.archive._best_effort_restart(
                    _string_list(detail.get("original_running")),
                    allow_startup_recovery=allow_startup_recovery,
                )
                status = (
                    "rolled_back"
                    if restart_error is None and cleanup_error is None
                    else "rollback_failed"
                )
                self._journal(
                    operation,
                    {
                        **detail,
                        "restart_error": restart_error,
                        "staging_cleanup_error": cleanup_error,
                    },
                    phase="aborted_before_switch",
                    status=status,
                )
                operation.transition(
                    "failed",
                    message="Upgrade interrupted before program switch",
                    detail={
                        **detail,
                        "recovered": True,
                        "rolled_back": restart_error is None,
                        "restart_error": restart_error,
                        "staging_cleanup_error": cleanup_error,
                    },
                )
                self.store.save(operation)
                recovered.append(
                    {"transaction_id": transaction_id, "action": status}
                )
                continue
            rollback = await self._rollback(
                operation,
                package,
                detail,
                allow_startup_recovery=allow_startup_recovery,
            )
            operation.transition(
                "failed",
                message="Interrupted upgrade automatically rolled back",
                detail={
                    **detail,
                    "recovered": True,
                    "rolled_back": rollback.get("succeeded", False),
                    "rollback_result": rollback,
                },
            )
            self.store.save(operation)
            recovered.append(
                {
                    "transaction_id": transaction_id,
                    "action": "rolled_back",
                    "result": rollback,
                }
            )
        return recovered

    async def _recover_update_guard_handoff(
        self,
        operation: ManagerOperation,
        package: VerifiedUpgradePackage | None,
        detail: dict[str, Any],
        *,
        validated_rollback_marker: dict[str, Any] | None = None,
        allow_startup_recovery: bool = False,
    ) -> dict[str, Any]:
        staged = dict(detail.get("platform_staged") or {})
        rollback_marker = Path(str(staged.get("rollback_marker", "")))
        marker = validated_rollback_marker
        marker_was_validated = marker is not None
        if marker is None:
            validator = getattr(
                self.platform_adapter, "validate_rollback_marker", None
            )
            if callable(validator):
                marker = validator(detail)
                marker_was_validated = marker is not None
            elif rollback_marker.is_file() and not rollback_marker.is_symlink():
                marker = _read_json_object(rollback_marker)
        if isinstance(marker, dict):
            marker_matches = marker.get("transaction_id") == detail["transaction_id"]
            if (
                marker_matches
                and marker.get("status") == "program_rollback_started"
            ):
                self._handoff_status = dict(marker)
                self.service.request_shutdown(
                    "windows_update_guard_rollback_pending"
                )
                return {
                    "action": "awaiting_guard_rollback",
                    "result": marker,
                }
            if marker_matches and marker.get("status") == "program_rolled_back":
                rollback = await self._rollback(
                    operation,
                    package,
                    detail,
                    program_already_restored=marker_was_validated,
                    allow_startup_recovery=allow_startup_recovery,
                )
                rollback_succeeded = rollback.get("succeeded") is True
                recovery_error = (
                    None
                    if rollback_succeeded
                    else str(
                        rollback.get("error")
                        or "Program rollback completed but data recovery failed"
                    )
                )
                operation.transition(
                    "failed",
                    message=(
                        "UpdateGuard restored the previous program and data"
                        if rollback_succeeded
                        else "Previous program is active but data recovery failed; "
                        "manual recovery is required"
                    ),
                    detail={
                        **detail,
                        "recovered": True,
                        "rolled_back": rollback_succeeded,
                        "rollback_status": (
                            "succeeded" if rollback_succeeded else "failed"
                        ),
                        "rollback_result": rollback,
                        **(
                            {"recovery_error": recovery_error}
                            if recovery_error is not None
                            else {}
                        ),
                    },
                )
                self.store.save(operation)
                if rollback_succeeded:
                    self.service.set_startup_maintenance_gate(False)
                    return {"action": "rolled_back", "result": rollback}
                return {
                    "action": "rollback_failed",
                    "result": rollback,
                    "manual_recovery_required": True,
                }
            if marker_matches and marker.get("status") == "program_rollback_failed":
                recovery_error = str(
                    marker.get("rollback_error")
                    or "UpdateGuard could not restore the previous program"
                )
                operation.transition(
                    "failed",
                    message=(
                        "UpdateGuard could not restore the previous program; "
                        "manual recovery is required"
                    ),
                    detail={
                        **detail,
                        "recovered": True,
                        "rolled_back": False,
                        "rollback_status": "failed",
                        "rollback_result": marker,
                        "recovery_error": recovery_error,
                    },
                )
                self.store.save(operation)
                return {
                    "action": "rollback_failed",
                    "result": marker,
                    "manual_recovery_required": True,
                }
        health_marker = Path(str(staged.get("health_marker", "")))
        health_marker_published = False
        try:
            started = self._publish_started_marker(detail)
            if started["status"] != "started":
                raise UpgradeCompatibilityError(started["error"])
            with self._maintenance_context(
                None,
                timeout=1,
                allow_startup_recovery=allow_startup_recovery,
            ) as maintenance:
                migrations = await asyncio.to_thread(
                    self.archive._migrate_and_validate_schema
                )
                await self.archive._restart(
                    maintenance, _string_list(detail.get("original_running"))
                )
                health = await self.archive._hard_health(
                    _string_list(detail.get("original_running")),
                    control_baseline=detail.get("control_heartbeat_baseline"),
                    control_gate=str(
                        detail.get("control_gate") or CONTROL_GATE_ENFORCED
                    ),
                )
            detail.update(
                {
                    "phase": "healthy",
                    "progress": 95,
                    "commit_point": "health_passed",
                    "migrations": migrations,
                    "health": health,
                }
            )
            self._journal(operation, detail, phase="healthy")
            marker = {
                "format_version": 2,
                "transaction_id": detail["transaction_id"],
                "target_version": detail["target_version"],
                "status": "healthy",
                "health": health,
                "manager_identity": started["manager_identity"],
                "updated_at": utc_now(),
            }
            _atomic_json(health_marker, marker)
            self._handoff_status = marker
            health_marker_published = True
            commit = await self.platform_adapter.commit(
                package,
                current=dict(detail.get("platform_current") or {}),
                staged=staged,
                transaction_id=str(detail["transaction_id"]),
            )
            detail["platform_commit"] = commit
            detail["phase"] = "committed"
            detail["progress"] = 100
            self._journal(
                operation, detail, phase="committed", status="committed"
            )
            operation.transition(
                "succeeded",
                message=f"Upgrade to {package.version} committed after UpdateGuard hand-off",
                detail={**detail, "recovered": True},
            )
            self.store.save(operation)
            self.store.retire_terminal_rollback_journals()
            self._prune_external_guard_cache(operation, detail)
            self.archive._apply_retention_if_safe()
            self.service.set_startup_maintenance_gate(False)
            return {"action": "committed"}
        except Exception as exc:
            if health_marker_published:
                # Publishing a healthy marker is the Windows program commit
                # decision.  Never tell the guard to downgrade after that
                # point; a later Manager can finalize the durable "healthy"
                # journal deterministically.
                self.service.set_startup_maintenance_gate(False)
                return {
                    "action": "healthy_marker_published",
                    "error": str(exc) or type(exc).__name__,
                }
            marker = {
                "format_version": 2,
                "transaction_id": detail["transaction_id"],
                "target_version": detail["target_version"],
                "status": "failed",
                "error": str(exc) or type(exc).__name__,
                "manager_identity": _current_process_identity(),
                "updated_at": utc_now(),
            }
            _atomic_json(health_marker, marker)
            self._handoff_status = marker
            asyncio.get_running_loop().call_later(
                0.25,
                self.service.request_shutdown,
                "windows_update_guard_rollback",
            )
            self._journal(
                operation,
                {**detail, "new_version_health_error": marker["error"]},
                phase="awaiting_update_guard",
                status="interrupted",
            )
            operation.transition(
                "failed",
                message=marker["error"],
                detail={
                    **detail,
                    "phase": "awaiting_program_rollback",
                    "new_version_health_error": marker["error"],
                    "rolled_back": False,
                },
            )
            self.store.save(operation)
            return {"action": "health_failed_waiting_guard", "error": marker["error"]}

    def _publish_started_marker(self, detail: dict[str, Any]) -> dict[str, Any]:
        staged = dict(detail.get("platform_staged") or {})
        path = Path(str(staged.get("started_marker", "")))
        if not path.is_absolute():
            raise UpgradeCompatibilityError(
                "UpdateGuard started marker path is unavailable"
            )
        identity = _current_process_identity()
        actual_version = get_version()
        target_version = str(detail.get("target_version") or "")
        valid_identity = _identity_belongs_to_instance(identity, self.layout.root)
        valid_version = (
            actual_version != "unknown"
            and target_version
            and Version(actual_version) == Version(target_version)
        )
        marker = {
            "format_version": 2,
            "transaction_id": detail["transaction_id"],
            "target_version": target_version,
            "actual_version": actual_version,
            "status": "started" if valid_identity and valid_version else "failed",
            "manager_identity": identity,
            "updated_at": utc_now(),
        }
        if not valid_identity:
            marker["error"] = "Updated Manager executable is outside instance root"
        elif not valid_version:
            marker["error"] = (
                f"Updated Manager version {actual_version!r} does not match "
                f"target {target_version!r}"
            )
        _atomic_json(path, marker)
        self._handoff_status = marker
        return marker

    def mark_api_ready(self) -> None:
        self._api_ready.set()

    async def wait_api_ready(self) -> None:
        await self._api_ready.wait()

    def handoff_health(self) -> dict[str, Any] | None:
        if self._handoff_status is not None:
            return dict(self._handoff_status)
        for journal in self.store.list_recoverable_journals():
            if journal.get("kind") != UPGRADE_JOURNAL_KIND:
                continue
            detail = dict(journal.get("detail") or {})
            if not self._is_guard_handoff(
                journal,
                detail,
                rollback_validator=getattr(
                    self.platform_adapter, "validate_rollback_marker", None
                ),
                health_validator=getattr(
                    self.platform_adapter, "validate_health_marker", None
                ),
            ):
                continue
            staged = dict(detail.get("platform_staged") or {})
            for name in (
                "rollback_marker",
                "health_marker",
                "started_marker",
            ):
                path = Path(str(staged.get(name, "")))
                if path.is_file() and not path.is_symlink():
                    marker = _read_json_object(path)
                    if (
                        marker.get("transaction_id") == detail.get("transaction_id")
                        and marker.get("target_version")
                        == detail.get("target_version")
                    ):
                        return marker
        return None

    def _is_guard_handoff(
        self,
        journal: dict[str, Any],
        detail: dict[str, Any],
        *,
        rollback_validator,
        health_validator,
    ) -> bool:
        if (
            not callable(rollback_validator)
            or not callable(health_validator)
            or journal.get("kind") != UPGRADE_JOURNAL_KIND
            or detail.get("commit_point") == "not_started"
        ):
            return False
        staged = detail.get("platform_staged")
        if not isinstance(staged, dict):
            return False
        return all(
            isinstance(staged.get(name), str) and staged[name]
            for name in (
                "started_marker",
                "health_marker",
                "rollback_marker",
            )
        )

    def _normalize_guard_handoff(
        self,
        operation: ManagerOperation,
        detail: dict[str, Any],
        *,
        marker: dict[str, Any] | None,
    ) -> None:
        detail["phase"] = "awaiting_update_guard"
        if isinstance(marker, dict):
            detail["guard_status"] = marker.get("status")
        self._journal(
            operation,
            detail,
            phase="awaiting_update_guard",
            status="interrupted",
        )
        operation.detail = dict(detail)
        self.store.save(operation)

    async def _rollback(
        self,
        operation: ManagerOperation,
        package: VerifiedUpgradePackage | None,
        detail: dict[str, Any],
        *,
        program_already_restored: bool = False,
        maintenance_lease: MaintenanceReservation | None = None,
        allow_startup_recovery: bool = False,
    ) -> dict[str, Any]:
        transaction_id = str(detail["transaction_id"])
        original = _string_list(detail.get("original_running"))
        if detail.get("commit_point") == "not_started":
            cleanup_error = await self._cleanup_platform_staging(detail)
            restart_error = await self.archive._best_effort_restart(
                original,
                maintenance_lease=maintenance_lease,
                allow_startup_recovery=allow_startup_recovery,
            )
            self._journal(
                operation,
                {
                    **detail,
                    "restart_error": restart_error,
                    "staging_cleanup_error": cleanup_error,
                },
                phase="rolled_back",
                status=(
                    "rolled_back"
                    if restart_error is None and cleanup_error is None
                    else "rollback_failed"
                ),
            )
            return {
                "succeeded": restart_error is None and cleanup_error is None,
                "program_restored": False,
                "data_restored": False,
                "restart_error": restart_error,
                "staging_cleanup_error": cleanup_error,
            }
        detail["rollback_status"] = "running"
        rollback_baseline, rollback_control_gate = (
            await self.archive._capture_control_baseline()
        )
        detail["rollback_control_heartbeat_baseline"] = rollback_baseline
        detail["rollback_control_gate"] = rollback_control_gate
        self._journal(operation, detail, phase="rolling_back")
        try:
            with self._maintenance_context(
                maintenance_lease,
                timeout=1,
                allow_startup_recovery=allow_startup_recovery,
            ) as maintenance:
                await self.archive._quiesce(maintenance)
                if program_already_restored:
                    program = {"already_restored_by_update_guard": True}
                else:
                    if package is None:
                        raise UpgradeError(
                            "Target package is required before program rollback"
                        )
                    program = await self.platform_adapter.rollback(
                        package,
                        current=dict(detail.get("platform_current") or {}),
                        staged=dict(detail.get("platform_staged") or {}),
                        transaction_id=transaction_id,
                    )
                pre = detail.get("pre_upgrade_filename")
                if not isinstance(pre, str):
                    raise UpgradeError("Pre-upgrade archive is unavailable")
                restored = await asyncio.to_thread(
                    apply_archive, pre, layout=self.layout
                )
                if restored["failed_entries"]:
                    raise ArchiveError(
                        str(
                            restored["failed_entries"][0].get("error")
                            or "Pre-upgrade data restore failed"
                        )
                    )
                migrations = await asyncio.to_thread(
                    self.archive._migrate_and_validate_schema
                )
                await self.archive._restart(maintenance, original)
                health = await self.archive._hard_health(
                    original,
                    control_baseline=rollback_baseline,
                    control_gate=rollback_control_gate,
                )
            result = {
                "succeeded": True,
                "program_restored": True,
                "data_restored": True,
                "program": program,
                "archive": pre,
                "restore": restored,
                "migrations": migrations,
                "health": health,
            }
            self._journal(
                operation,
                {**detail, "rollback_result": result},
                phase="rolled_back",
                status="rolled_back",
            )
            self.archive._apply_retention_if_safe()
            return result
        except Exception as exc:
            cleanup_error = await self._cleanup_platform_staging(detail)
            result = {
                "succeeded": False,
                "error": str(exc) or type(exc).__name__,
                "staging_cleanup_error": cleanup_error,
            }
            self._journal(
                operation,
                {**detail, "rollback_result": result},
                phase="rollback_failed",
                status="rollback_failed",
            )
            return result

    async def _cleanup_platform_staging(
        self, detail: dict[str, Any]
    ) -> str | None:
        cleanup = getattr(self.platform_adapter, "cleanup", None)
        if not callable(cleanup):
            return None
        try:
            await cleanup(dict(detail.get("platform_staged") or {}))
        except Exception as exc:
            return str(exc) or type(exc).__name__
        return None

    def _verified_package(self, version: str | None) -> VerifiedUpgradePackage:
        release_status = self.release_manager.status()
        available = release_status.get("available")
        if not isinstance(available, dict):
            raise ReleaseDownloadError("Check for an update before installing")
        target_version = str(version or available.get("version") or "")
        if target_version != available.get("version"):
            raise UpgradeCompatibilityError(
                "Only the current discovered candidate may be installed"
            )
        return self._package_from_release(target_version, available)

    def _package_from_release(
        self,
        target_version: str,
        available: dict[str, Any],
    ) -> VerifiedUpgradePackage:
        if target_version != available.get("version"):
            raise UpgradeCompatibilityError(
                "Release snapshot version does not match the upgrade transaction"
            )
        compatibility = available.get("compatibility")
        if (
            available.get("compatible") is not True
            or not isinstance(compatibility, dict)
            or compatibility.get("automatic_upgrade") is not True
        ):
            problems = (
                compatibility.get("problems", [])
                if isinstance(compatibility, dict)
                else []
            )
            reason = "; ".join(str(item) for item in problems) or (
                "Release requires manual deployment"
            )
            raise UpgradeCompatibilityError(reason)
        if compatibility.get("deployment_schema_version") != DEPLOYMENT_SCHEMA_VERSION:
            raise UpgradeCompatibilityError(
                "Deployment schema changes require manual deployment"
            )
        if Version(str(compatibility.get("minimum_manager_version"))) > Version(
            MANAGER_VERSION
        ):
            raise UpgradeCompatibilityError(
                "Release requires a Manager upgrade, which is not automatic"
            )
        version_dir = self.layout.manager_packages_dir / target_version
        metadata_path = version_dir / "verified-release.json"
        try:
            metadata = _read_json_object(metadata_path)
            artifact = dict(metadata["artifact"])
            filename = str(artifact["filename"])
            path = version_dir / filename
            available_artifact = next(
                (
                    {
                        key: item[key]
                        for key in (
                            "platform",
                            "arch",
                            "filename",
                            "purpose",
                            "size",
                            "sha256",
                        )
                    }
                    for item in available.get("artifacts", [])
                    if isinstance(item, dict)
                    and item.get("purpose") == artifact.get("purpose")
                    and all(
                        key in item
                        for key in (
                            "platform",
                            "arch",
                            "filename",
                            "purpose",
                            "size",
                            "sha256",
                        )
                    )
                ),
                None,
            )
            if (
                metadata.get("version") != target_version
                or metadata.get("compatibility") != compatibility
                or metadata.get("change_scope") != available.get("change_scope")
                or metadata.get("verified_path") != filename
                or artifact != available_artifact
                or path.is_symlink()
                or not path.is_file()
                or path.stat().st_size != artifact["size"]
                or _sha256_file(path) != artifact["sha256"]
            ):
                raise UpgradeCompatibilityError(
                    "Downloaded package no longer matches verified release metadata"
                )
            if artifact.get("platform") == "windows":
                if artifact.get("purpose") != "velopack-full":
                    raise UpgradeCompatibilityError(
                        "Windows automatic updates require the Velopack full package"
                    )
                companions = metadata.get("companions")
                if not isinstance(companions, list):
                    raise UpgradeCompatibilityError(
                        "Verified Velopack feed metadata is missing"
                    )
                expected_by_purpose = {
                    item.get("purpose"): {
                        key: item[key]
                        for key in (
                            "platform",
                            "arch",
                            "filename",
                            "purpose",
                            "size",
                            "sha256",
                        )
                    }
                    for item in available.get("artifacts", [])
                    if isinstance(item, dict)
                    and all(
                        key in item
                        for key in (
                            "platform",
                            "arch",
                            "filename",
                            "purpose",
                            "size",
                            "sha256",
                        )
                    )
                }
                seen_companions: set[str] = set()
                for companion in companions:
                    companion_artifact = (
                        companion.get("artifact")
                        if isinstance(companion, dict)
                        else None
                    )
                    companion_name = (
                        companion.get("verified_path")
                        if isinstance(companion, dict)
                        else None
                    )
                    if not isinstance(companion_artifact, dict):
                        raise UpgradeCompatibilityError(
                            "Verified Velopack companion metadata is invalid"
                        )
                    purpose = companion_artifact.get("purpose")
                    if (
                        purpose not in {"velopack-releases", "velopack-assets"}
                        or companion_artifact != expected_by_purpose.get(purpose)
                        or companion_name != companion_artifact.get("filename")
                    ):
                        raise UpgradeCompatibilityError(
                            "Velopack feed does not match the current Release"
                        )
                    companion_path = version_dir / str(companion_name)
                    if (
                        companion_path.is_symlink()
                        or not companion_path.is_file()
                        or companion_path.stat().st_size
                        != companion_artifact.get("size")
                        or _sha256_file(companion_path)
                        != companion_artifact.get("sha256")
                    ):
                        raise UpgradeCompatibilityError(
                            "Verified Velopack feed asset changed after download"
                        )
                    try:
                        feed = json.loads(
                            companion_path.read_text(encoding="utf-8")
                        )
                    except (OSError, ValueError) as exc:
                        raise UpgradeCompatibilityError(
                            "Velopack feed asset is invalid JSON"
                        ) from exc
                    # Velopack publishes the releases feed as a JSON object
                    # ({"Assets": [...]}) but the assets feed as a bare array.
                    expected_root = (
                        dict if purpose == "velopack-releases" else list
                    )
                    if not isinstance(feed, expected_root):
                        raise UpgradeCompatibilityError(
                            "Velopack releases feed must be a JSON object"
                            if purpose == "velopack-releases"
                            else "Velopack assets feed must be a JSON array"
                        )
                    seen_companions.add(str(purpose))
                if seen_companions != {"velopack-releases", "velopack-assets"}:
                    raise UpgradeCompatibilityError(
                        "Both Velopack release and asset feeds are required"
                    )
        except (OSError, KeyError, TypeError, ValueError) as exc:
            if isinstance(exc, UpgradeCompatibilityError):
                raise
            raise UpgradeCompatibilityError(
                "A verified package for the current candidate is required"
            ) from exc
        return VerifiedUpgradePackage(
            version=target_version,
            platform=str(artifact["platform"]),
            arch=str(artifact["arch"]),
            path=path,
            metadata_path=metadata_path,
            artifact=artifact,
            release={**available, "fallbacks": metadata.get("fallbacks", {})},
        )

    def _record_running(
        self,
        operation: ManagerOperation,
        detail: dict[str, Any],
        running: list[str],
    ) -> None:
        detail["original_running"] = list(running)
        self._journal(operation, detail, phase="quiescing")

    def _phase(
        self,
        operation: ManagerOperation,
        detail: dict[str, Any],
        phase: str,
        progress: int,
    ) -> None:
        detail["phase"] = phase
        detail["progress"] = progress
        operation.transition("running", detail=detail)
        self.store.save(operation)
        self._journal(operation, detail, phase=phase)

    def _journal(
        self,
        operation: ManagerOperation,
        detail: dict[str, Any],
        *,
        phase: str | None = None,
        status: str = "running",
    ) -> None:
        self.store.write_journal(
            str(detail["transaction_id"]),
            kind=UPGRADE_JOURNAL_KIND,
            phase=phase or str(detail.get("phase", "preflight")),
            status=status,
            operation_id=operation.operation_id,
            detail=detail,
        )

    def _prune_external_guard_cache(
        self, operation: ManagerOperation, detail: dict[str, Any]
    ) -> dict[str, Any]:
        """Best-effort cleanup of stale external UpdateGuard versions.

        Runs only once no journal still needs recovery; any failure degrades
        to a journal-visible warning.  Never raises: a cleanup problem must
        not affect the committed upgrade it runs after.
        """
        digest_loader = getattr(
            self.platform_adapter, "stable_guard_digest", None
        )
        prune = getattr(
            self.platform_adapter, "prune_external_guard_cache", None
        )
        if digest_loader is None or prune is None:
            return {}
        try:
            if self.store.list_recoverable_journals():
                result = {"guard_cache_prune_skipped": "recoverable_journals"}
            else:
                keep_digest = digest_loader()
                if keep_digest is None:
                    return {}
                removed = prune(keep_digest)
                result = {"guard_cache_pruned": removed} if removed else {}
        except Exception as exc:
            result = {
                "guard_cache_prune_error": str(exc) or type(exc).__name__
            }
        if not result:
            return {}
        try:
            self._journal(
                operation,
                {**detail, **result},
                phase="committed",
                status="committed",
            )
        except Exception:
            pass
        return result

    def _fault(self, phase: str) -> None:
        if self.fault_hook is not None:
            self.fault_hook(phase)


def _validate_linux_manifest(
    manifest: dict[str, Any], package: VerifiedUpgradePackage
) -> None:
    required = {
        "format_version",
        "version",
        "platform",
        "arch",
        "deployment_schema_version",
        "minimum_manager_version",
        "catalog_version",
        "catalog_digest",
        "automatic_upgrade",
        "change_scope",
        "compose",
        "image_archive",
        "images",
    }
    if set(manifest) != required:
        raise UpgradeCompatibilityError("Linux package manifest fields mismatch")
    if (
        manifest["format_version"] != LINUX_PACKAGE_FORMAT
        or manifest["version"] != package.version
        or manifest["platform"] != "linux"
        or manifest["arch"] != package.arch
    ):
        raise UpgradeCompatibilityError("Linux package target/version mismatch")
    if manifest["automatic_upgrade"] is not True:
        raise UpgradeCompatibilityError(
            "Release disables automatic upgrade; manual deployment is required"
        )
    change_scope = manifest["change_scope"]
    outer_scope = package.release.get("change_scope")
    if (
        not isinstance(change_scope, list)
        or any(type(item) is not str or not item for item in change_scope)
        or change_scope != outer_scope
    ):
        raise UpgradeCompatibilityError(
            "Linux package change_scope differs from the verified Release contract"
        )
    if "manager" in change_scope:
        raise UpgradeCompatibilityError(
            "A Release that changes Manager requires manual deployment",
            code="manager_upgrade_requires_manual_deployment",
        )
    if manifest["deployment_schema_version"] != DEPLOYMENT_SCHEMA_VERSION:
        raise UpgradeCompatibilityError(
            "Deployment schema changes require manual deployment"
        )
    if Version(str(manifest["minimum_manager_version"])) > Version(MANAGER_VERSION):
        raise UpgradeCompatibilityError(
            "Release requires a Manager upgrade, which is not automatic"
        )
    catalog = DATA_CATALOG.to_dict()
    if (
        manifest["catalog_version"] != catalog["format_version"]
        or manifest["catalog_digest"] != DATA_CATALOG.digest
    ):
        raise UpgradeCompatibilityError("DataAsset Catalog is incompatible")
    images = manifest["images"]
    if (
        not isinstance(images, list)
        or len(images) != 2
        or any(
            not isinstance(item, dict)
            or set(item) != {"role", "reference", "image_id"}
            or item["role"] not in {"bot", "dashboard"}
            or not isinstance(item["reference"], str)
            or not item["reference"].startswith("ghcr.io/pear-studio/")
            or not isinstance(item["image_id"], str)
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", item["image_id"])
            for item in images
        )
        or {item["role"] for item in images} != {"bot", "dashboard"}
        or any(
            (
                "nonebot-dicepp"
                if item["role"] == "bot"
                else "dicepp-dashboard"
            )
            not in item["reference"]
            for item in images
        )
        or len({item["reference"] for item in images}) != 2
        or len({item["image_id"] for item in images}) != 2
    ):
        raise UpgradeCompatibilityError("Linux package image list is invalid")
    for key in ("compose", "image_archive"):
        record = manifest[key]
        if (
            not isinstance(record, dict)
            or set(record) != {"path", "size", "sha256"}
            or not _safe_member_name(record["path"])
            or type(record["size"]) is not int
            or record["size"] <= 0
            or not _is_sha256(record["sha256"])
        ):
            raise UpgradeCompatibilityError(f"Linux package {key} record is invalid")
    if manifest["image_archive"]["size"] > MAX_LINUX_IMAGE_ARCHIVE_BYTES:
        raise UpgradeCompatibilityError(
            "Linux image archive exceeds the automatic-upgrade size limit"
        )


def _compose_topology(text: str) -> dict[str, Any]:
    try:
        import yaml

        value = yaml.safe_load(text)
    except Exception as exc:
        raise UpgradeCompatibilityError("Compose file cannot be parsed") from exc
    if not isinstance(value, dict):
        raise UpgradeCompatibilityError("Compose root must be an object")
    normalized = _normalize_compose_value(value)
    normalized.pop("version", None)
    services = normalized.get("services")
    if not isinstance(services, dict):
        raise UpgradeCompatibilityError("Compose services are missing")
    for name, definition in services.items():
        if not isinstance(name, str) or not isinstance(definition, dict):
            raise UpgradeCompatibilityError("Compose service is invalid")
        if name in {"bot", "dashboard"}:
            definition.pop("image", None)
            definition.pop("build", None)
    return normalized


def _normalize_compose_value(value: Any) -> Any:
    """Return a deterministic deep Compose representation.

    Only mapping key order is semantically irrelevant.  List order and every
    nested mount/network option remain part of the compatibility boundary.
    """
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise UpgradeCompatibilityError("Compose mapping keys must be strings")
        return {
            key: _normalize_compose_value(value[key])
            for key in sorted(value)
        }
    if isinstance(value, list):
        return [_normalize_compose_value(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise UpgradeCompatibilityError("Compose contains an unsupported value")


def _read_zip_json(archive: zipfile.ZipFile, name: str, limit: int) -> dict[str, Any]:
    raw = _read_zip_member(archive, name, limit)
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UpgradeCompatibilityError(f"{name} is invalid JSON") from exc
    if not isinstance(value, dict):
        raise UpgradeCompatibilityError(f"{name} root must be an object")
    return value


def _read_zip_member(archive: zipfile.ZipFile, name: str, limit: int) -> bytes:
    try:
        info = archive.getinfo(name)
    except KeyError as exc:
        raise UpgradeCompatibilityError(f"Linux bundle is missing {name}") from exc
    if info.file_size > limit:
        raise UpgradeCompatibilityError(f"{name} exceeds size limit")
    with archive.open(info, "r") as handle:
        raw = handle.read(limit + 1)
    if len(raw) > limit:
        raise UpgradeCompatibilityError(f"{name} exceeds size limit")
    return raw


def _validate_zip_members(archive: zipfile.ZipFile) -> None:
    members = archive.infolist()
    if len(members) > MAX_LINUX_MEMBER_COUNT:
        raise UpgradeCompatibilityError("Linux bundle has too many members")
    total = 0
    seen: set[str] = set()
    for info in members:
        if not _safe_member_name(info.filename) or info.filename in seen:
            raise UpgradeCompatibilityError(
                f"Unsafe or duplicate Linux bundle path: {info.filename!r}"
            )
        seen.add(info.filename)
        mode = info.external_attr >> 16
        if stat.S_ISLNK(mode):
            raise UpgradeCompatibilityError("Linux bundle must not contain symlinks")
        total += info.file_size
        if total > MAX_LINUX_TOTAL_UNCOMPRESSED_BYTES:
            raise UpgradeCompatibilityError(
                "Linux bundle exceeds the uncompressed size limit"
            )


def _parse_checksums(raw: bytes) -> dict[str, str]:
    result: dict[str, str] = {}
    try:
        lines = raw.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise UpgradeCompatibilityError("checksums.sha256 is not UTF-8") from exc
    for line in lines:
        if not line.strip():
            continue
        digest, separator, name = line.partition("  ")
        if (
            not separator
            or not _is_sha256(digest)
            or not _safe_member_name(name)
            or name in result
        ):
            raise UpgradeCompatibilityError("checksums.sha256 contains an invalid row")
        result[name] = digest
    if not result:
        raise UpgradeCompatibilityError("checksums.sha256 is empty")
    return result


def _verify_zip_record(archive: zipfile.ZipFile, record: dict[str, Any]) -> None:
    try:
        info = archive.getinfo(record["path"])
    except KeyError as exc:
        raise UpgradeCompatibilityError(
            f"Linux bundle is missing {record['path']}"
        ) from exc
    if info.file_size != record["size"]:
        raise UpgradeCompatibilityError(
            f"Linux bundle size differs: {record['path']}"
        )
    digest = hashlib.sha256()
    with archive.open(info, "r") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != record["sha256"]:
        raise UpgradeCompatibilityError(
            f"Linux bundle digest differs: {record['path']}"
        )


def _safe_extract_member(
    archive: zipfile.ZipFile, name: str, directory: Path
) -> Path:
    if not _safe_member_name(name):
        raise UpgradeCompatibilityError("Unsafe Linux bundle extraction path")
    target = directory.joinpath(*PurePosixPath(name).parts)
    target.parent.mkdir(parents=True, exist_ok=True)
    with archive.open(name, "r") as source, target.open("xb") as output:
        shutil.copyfileobj(source, output, 1024 * 1024)
        output.flush()
        os.fsync(output.fileno())
    return target


def _safe_member_name(name: Any) -> bool:
    if not isinstance(name, str) or not name or "\\" in name or "\x00" in name:
        return False
    path = PurePosixPath(name)
    return not path.is_absolute() and all(part not in {"", ".", ".."} for part in path.parts)


def _validate_process_identity(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise UpgradeCompatibilityError("Manager process identity is unavailable")
    try:
        pid = value["pid"]
        started_at = value["started_at"]
        executable = value["executable"]
    except KeyError as exc:
        raise UpgradeCompatibilityError(
            "Manager process identity must include PID/start time/executable"
        ) from exc
    if (
        type(pid) is not int
        or pid <= 0
        or not isinstance(started_at, str)
        or not started_at
        or not isinstance(executable, str)
        or not Path(executable).is_absolute()
    ):
        raise UpgradeCompatibilityError("Manager process identity is invalid")
    return {"pid": pid, "started_at": started_at, "executable": executable}


def _current_process_identity() -> dict[str, Any]:
    # Import lazily: UpdateGuard shares validation helpers from this module.
    from .update_guard import current_process_identity as inspect_current

    return inspect_current()


def _identity_belongs_to_instance(
    identity: dict[str, Any], instance_root: Path
) -> bool:
    executable = Path(identity["executable"])
    try:
        return executable.resolve(strict=False).is_relative_to(
            instance_root.resolve()
        )
    except OSError:
        return False


def _nupkg_version(path: Path) -> str | None:
    try:
        with zipfile.ZipFile(path) as archive:
            nuspecs = [
                name
                for name in archive.namelist()
                if PurePosixPath(name).suffix.casefold() == ".nuspec"
            ]
            if len(nuspecs) != 1:
                return None
            root = ElementTree.fromstring(archive.read(nuspecs[0]))
    except (
        OSError,
        zipfile.BadZipFile,
        ElementTree.ParseError,
        KeyError,
    ):
        return None
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1] == "version":
            value = (element.text or "").strip()
            return value or None
    return None






def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_sha256(value: Any) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return value == value.lower()


def _string_list(value: Any) -> list[str]:
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []
