"""Confirmed, durable DicePP program upgrade transactions.

The coordinator owns data safety and transaction recovery.  Platform adapters
own only the program switch: Docker images on Linux and the explicit Velopack
hand-off on Windows.  External services are deliberately outside hard health.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import secrets
import shutil
import socket
import stat
import zipfile
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
from .archive_housekeeping import ArchiveHousekeeping
from .archive_coordinator import ArchiveCoordinator
from ._file_utils import _atomic_json, _read_json_object
from .deployment import (
    DEPLOYMENT_SCHEMA_VERSION,
    MANAGER_DEFAULT_PORT,
    MANAGER_VERSION,
    RUNTIME_UNIT_LABEL,
)
from .docker_runtime import DockerRuntimeError
from .linux_handoff import CURRENT_ALIAS_NAMES
from .linux_handoff_coordinator import LinuxHandoffCoordinator
from .models import ManagerOperation, utc_now
from .maintenance_policy import is_terminal_rollback_failure
from .maintenance_runtime import CONTROL_GATE_ENFORCED, MaintenanceRuntimeSupport
from ._path_security import (
    assert_contained_no_reparse,
    assert_directory_no_reparse,
    is_reparse_point,
    open_regular_binary_no_follow,
)
from .release import (
    MAX_LINUX_BUNDLE_BYTES,
    ReleaseDownloadError,
    ReleaseManager,
)
from .velopack_bundle import (
    VELOPACK_BUNDLE_NAME,
    VelopackBundleError,
    validate_velopack_bundle,
)
from .service import MaintenanceReservation, ManagerService

UPGRADE_JOURNAL_KIND = "upgrade"
CONFIRMATION_FORMAT = 1
LINUX_PACKAGE_FORMAT = 1
LINUX_MANAGER_HANDOFF_FORMAT = 1
MAX_INNER_MANIFEST_BYTES = 1024 * 1024
MAX_CHECKSUMS_BYTES = 2 * 1024 * 1024
MAX_LINUX_IMAGE_ARCHIVE_BYTES = 15 * 1024**3
MAX_LINUX_TOTAL_UNCOMPRESSED_BYTES = 16 * 1024**3
MAX_LINUX_MEMBER_COUNT = 10_000

#: Manager handoff 期限(秒)。目标 Manager 启动/接管期限与整个本地事务
#: 期限;数值由真实候选矩阵测量后收紧,当前保留保守裕量,不做用户配置。
LINUX_STARTUP_DEADLINE_SECONDS = 300
LINUX_TRANSACTION_DEADLINE_SECONDS = 3600
LINUX_STAGE_RESERVE_BYTES = 256 * 1024**2
CONFIRMATION_TTL = timedelta(minutes=15)
_TERMINAL = {"succeeded", "failed", "rejected", "interrupted"}
_VELOPACK_GENERATION_ID_RE = re.compile(r"^[0-9a-f]{32}$")
SHUTDOWN_RUNTIME_POLICY_FIELD = "shutdown_runtime_policy"
SHUTDOWN_RUNTIME_KEEP = "keep"
SHUTDOWN_RUNTIME_QUIESCE = "quiesce"
_LEGACY_WINDOWS_SHUTDOWN_QUIESCE_PHASES = {
    "target_health_failed",
    "switch_identity_unknown",
    "manual_restore_blocked",
    "manual_data_restore",
    "manual_restore_failed",
}


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
    bundle_path: Path | None = None
    bundle_manifest: dict[str, Any] | None = None

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

class LinuxUpgradeExecutor(Protocol):
    """Fixed-operation Docker boundary used after the bundle is verified."""

    async def capture_images(
        self, image_records: list[dict[str, str]]
    ) -> dict[str, Any]: ...

    async def inspect_tag(self, reference: str) -> dict[str, Any]: ...

    async def inspect_tag_optional(
        self, reference: str
    ) -> dict[str, Any] | None: ...

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

    async def restore_images(
        self,
        previous: dict[str, Any],
        *,
        transaction_id: str | None = None,
    ) -> dict[str, Any]: ...


class LinuxBundleUpgradeAdapter:
    """Verify the two-layer Linux contract and delegate fixed Docker actions."""

    platform = "linux"
    protocol = "linux-manager-handoff-v1"
    supported = True

    def __init__(
        self,
        *,
        layout: InstanceLayout,
        executor: LinuxUpgradeExecutor,
        handoff_executor: Any | None = None,
        current_compose: Path | None = None,
    ) -> None:
        self.layout = layout
        self.executor = executor
        self.handoff = handoff_executor
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
        captured = await self.executor.capture_images(list(manifest["images"]))
        if self.handoff is None:
            raise UpgradeCompatibilityError(
                "Linux Manager handoff executor is unavailable",
                code="manager_handoff_unavailable",
            )
        manager = await self.executor.capture_manager(captured["project"])
        aliases = await self._capture_current_aliases(captured, manager)
        captured["manager"] = manager
        captured["current_aliases"] = aliases
        # The source version is the running Manager's own version; it becomes
        # the recovery contract's ``source_version``.  Omit it when the
        # installed package cannot report a version so recovery fails closed
        # instead of binding a bogus source.
        source_version = get_version()
        if source_version != "unknown":
            captured["source_version"] = source_version
        return captured

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

    async def _capture_current_aliases(
        self, captured: dict[str, Any], manager: dict[str, Any]
    ) -> dict[str, dict[str, str]]:
        """Initialize or verify fixed aliases before any upgrade mutation.

        Container capture has already proved the current Compose identities,
        while runtime quiescing and image switching have not started yet.
        Existing aliases are checked for drift before either missing alias is
        created, so a retry can safely converge after a partial Docker error.
        """
        if self.handoff is None:
            raise UpgradeCompatibilityError(
                "Linux Manager handoff executor is unavailable",
                code="manager_handoff_unavailable",
            )
        aliases: dict[str, dict[str, str]] = {}
        sources = self._managed_alias_sources(captured["containers"], manager)
        missing: list[str] = []
        for role, target in sources.items():
            reference = CURRENT_ALIAS_NAMES[role]
            payload = await self.executor.inspect_tag_optional(reference)
            expected = target["image_id"]
            if payload is None:
                missing.append(role)
                aliases[role] = {"name": reference, "image_id": expected}
                continue
            image_id = payload.get("Id")
            if not isinstance(image_id, str) or not re.fullmatch(
                r"sha256:[0-9a-f]{64}", image_id
            ):
                raise UpgradeCompatibilityError(
                    f"local alias {reference} cannot be resolved",
                    code="current_alias_unresolved",
                )
            if image_id != expected:
                raise UpgradeCompatibilityError(
                    f"local alias {reference} does not point at the managed "
                    f"{role} image; manual repair required",
                    code="current_alias_drifted",
                )
            aliases[role] = {"name": reference, "image_id": expected}

        for role in missing:
            alias = aliases[role]
            repo, tag = alias["name"].rsplit(":", 1)
            await self.handoff.tag_image(alias["image_id"], repo, tag)
            payload = await self.executor.inspect_tag(alias["name"])
            if payload.get("Id") != alias["image_id"]:
                raise UpgradeCompatibilityError(
                    f"local alias {alias['name']} could not be initialized",
                    code="current_alias_bootstrap_failed",
                )
        return aliases

    @staticmethod
    def _managed_alias_sources(
        containers: dict[str, Any], manager: dict[str, Any]
    ) -> dict[str, dict[str, Any]]:
        dashboard = containers["dashboard"]
        if dashboard.get("image_id") != manager.get("image_id"):
            raise UpgradeCompatibilityError(
                "Dashboard and Manager do not use the same managed image",
                code="current_alias_invalid",
            )
        return {
            "bot": containers["bot"],
            "dashboard_manager": manager,
        }

    async def prepare_recovery(
        self,
        staged: dict[str, Any],
        *,
        transaction_id: str,
        source_version: str,
        target_version: str,
        pre_upgrade_filename: str,
        original_running: list[str],
    ) -> dict[str, Any]:
        """Persist the immutable handoff request before starting the Updater."""
        current = staged["current"]
        manager = current["manager"]
        target_images = {
            role: staged["images"][role]["image_id"]
            for role in ("bot", "dashboard")
        }
        target_manager_image_id = staged["images"]["dashboard"]["image_id"]
        captured_original_running = self._captured_original_running(
            current, original_running
        )
        tx_dir = self.layout.manager_recovery_dir / transaction_id
        tx_dir.mkdir(parents=True, exist_ok=True)
        from .linux_handoff import (
            _REQUEST_FILENAME,
            write_request,
        )

        # The Dashboard is already quiesced (the coordinator stops it before
        # capture), so a SQLite backup snapshot here is a consistent recovery
        # material.  Missing or corrupt dashboard.db fails closed: the
        # transaction must not start without a restorable database snapshot.
        dashboard_snapshot = staged.get("dashboard_db")
        if not isinstance(dashboard_snapshot, dict):
            from .dashboard_db import DashboardDbError, snapshot_for_transaction

            try:
                dashboard_snapshot = snapshot_for_transaction(
                    self.layout, transaction_id
                )
            except DashboardDbError as exc:
                raise UpgradeCompatibilityError(
                    f"Dashboard database snapshot is unavailable: {exc}",
                    code="dashboard_db_snapshot_failed",
                ) from exc
            staged["dashboard_db"] = dashboard_snapshot
        dashboard_db_path = str(dashboard_snapshot.get("path", "dashboard.db"))
        dashboard_db_sha = str(dashboard_snapshot.get("sha256", "0" * 64))
        if not re.fullmatch(r"[0-9a-f]{64}", dashboard_db_sha):
            raise UpgradeCompatibilityError(
                "Dashboard database snapshot digest is invalid",
                code="dashboard_db_snapshot_failed",
            )
        from datetime import datetime, timezone

        payload = {
            "format_version": 1,
            "transaction_id": transaction_id,
            "operation_id": str(staged.get("operation_id") or ""),
            "source_version": source_version,
            "target_version": target_version,
            "compose_project": current.get("project", ""),
            "manager": {
                "container_id": manager["container_id"],
                "name": manager["name"],
                "backup_name": f"{manager['name']}.{transaction_id[:8]}",
                "image_id": manager["image_id"],
            },
            "target_manager_image_id": target_manager_image_id,
            "bot": {
                "container_id": current["containers"]["bot"]["container_id"],
                "image_id": current["containers"]["bot"]["image_id"],
            },
            "dashboard": {
                "container_id": current["containers"]["dashboard"]["container_id"],
                "image_id": current["containers"]["dashboard"]["image_id"],
            },
            "target_images": target_images,
            "pre_upgrade_archive": pre_upgrade_filename,
            "dashboard_db": {
                "path": dashboard_db_path,
                "sha256": dashboard_db_sha,
            },
            "original_running": captured_original_running,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "startup_deadline_seconds": LINUX_STARTUP_DEADLINE_SECONDS,
            "transaction_deadline_seconds": LINUX_TRANSACTION_DEADLINE_SECONDS,
            "current_aliases": current["current_aliases"],
            "restart_policies": {
                "manager": manager.get("restart_policy") or "unless-stopped",
                "bot": (
                    current["containers"]["bot"].get("restart_policy")
                    or "unless-stopped"
                ),
                "dashboard": (
                    current["containers"]["dashboard"].get("restart_policy")
                    or "unless-stopped"
                ),
            },
            "labels": {
                "transaction": "io.dicepp.upgrade-transaction",
                "role": "io.dicepp.upgrade-role",
            },
        }
        write_request(
            tx_dir / _REQUEST_FILENAME,
            payload,
            root=self.layout.manager_recovery_dir,
        )
        return {
            **staged,
            "transaction_dir": str(tx_dir),
            "request": payload,
        }

    @staticmethod
    def _captured_original_running(
        current: dict[str, Any], original_running: list[str]
    ) -> dict[str, bool]:
        """Map generic RuntimeUnit state to the exact captured containers.

        ``original_running`` contains Manager RuntimeUnit ids, not Docker
        container names.  The Bot's captured Docker config carries the trusted
        RuntimeUnit label used by the runtime adapter, while Dashboard is not
        a RuntimeUnit and therefore keeps its replacement-pre capture state.
        Bot ``running`` cannot be used here because capture happens after the
        coordinator has quiesced that RuntimeUnit.
        """
        containers = current.get("containers")
        if not isinstance(containers, dict):
            raise UpgradeCompatibilityError(
                "Captured Linux runtime containers are unavailable",
                code="original_runtime_state_invalid",
            )
        bot = containers.get("bot")
        dashboard = containers.get("dashboard")
        if not isinstance(bot, dict) or not isinstance(dashboard, dict):
            raise UpgradeCompatibilityError(
                "Captured Linux runtime container state is incomplete",
                code="original_runtime_state_invalid",
            )
        config = bot.get("config")
        labels = config.get("Labels") if isinstance(config, dict) else None
        runtime_unit_id = (
            labels.get(RUNTIME_UNIT_LABEL) if isinstance(labels, dict) else None
        )
        dashboard_running = dashboard.get("running")
        if (
            not isinstance(runtime_unit_id, str)
            or not runtime_unit_id
            or type(dashboard_running) is not bool
            or any(not isinstance(value, str) for value in original_running)
        ):
            raise UpgradeCompatibilityError(
                "Captured Linux runtime state cannot be bound to the handoff",
                code="original_runtime_state_invalid",
            )
        return {
            "bot": runtime_unit_id in set(original_running),
            "dashboard": dashboard_running,
        }

    async def switch(
        self,
        package: VerifiedUpgradePackage,
        *,
        current: dict[str, Any],
        staged: dict[str, Any],
        transaction_id: str,
    ) -> dict[str, Any]:
        del package
        if self.handoff is None:
            raise UpgradeCompatibilityError(
                "Linux Manager handoff executor is unavailable",
                code="manager_handoff_unavailable",
            )
        tx_dir = Path(staged["transaction_dir"])
        manager_identity = await self.handoff.inspect(
            current["manager"]["container_id"]
        )
        try:
            host_tx_dir = self.handoff.resolve_host_bind_source(
                manager_identity,
                container_root=self.layout.manager_recovery_dir,
                container_path=tx_dir,
            )
        except DockerRuntimeError as exc:
            raise UpgradeCompatibilityError(
                "Linux recovery directory is not backed by the expected "
                "writable host bind",
                code="manager_handoff_mount_invalid",
            ) from exc
        # Fail closed before the Updater exists: the old Manager must not
        # auto-restart itself (or the old bot/dashboard) while the Updater is
        # mid-transaction.  The Updater's own restart=no settings remain
        # (idempotent).
        for role, container_id in (
            ("manager", current["manager"]["container_id"]),
            ("bot", current["containers"]["bot"]["container_id"]),
            ("dashboard", current["containers"]["dashboard"]["container_id"]),
        ):
            if not isinstance(container_id, str) or not container_id:
                raise UpgradeCompatibilityError(
                    f"current {role} container identity is unavailable",
                    code="manager_handoff_unavailable",
                )
            await self.handoff.set_restart_policy(container_id, "no")
        source_image_id = current["manager"]["image_id"]
        updater_config = {
            "Image": source_image_id,
            "Cmd": [
                "python",
                "-m",
                "dicepp_manager.linux_update_helper",
                "--transaction-dir",
                "/transaction",
                "--socket",
                "/var/run/docker.sock",
            ],
            "HostConfig": {
                "Mounts": [
                    {
                        "Type": "bind",
                        "Source": host_tx_dir,
                        "Target": "/transaction",
                        "ReadOnly": False,
                    },
                    {
                        "Type": "bind",
                        "Source": "/var/run/docker.sock",
                        "Target": "/var/run/docker.sock",
                        "ReadOnly": False,
                    },
                ],
                "RestartPolicy": {"Name": "no"},
            },
        }
        updater_id = await self.handoff.create(
            f"dicepp-updater.{transaction_id[:8]}",
            updater_config,
            extra_labels={
                "io.dicepp.upgrade-transaction": transaction_id,
                "io.dicepp.upgrade-role": "updater",
            },
            restart_policy="no",
        )
        await self.handoff.start(updater_id)
        return {
            "handoff_required": True,
            "shutdown_required": False,
            "updater": updater_id,
        }

    async def rollback(
        self,
        package: VerifiedUpgradePackage,
        *,
        current: dict[str, Any],
        staged: dict[str, Any],
        transaction_id: str,
    ) -> dict[str, Any]:
        del package
        try:
            return await self.executor.restore_images(
                current, transaction_id=transaction_id
            )
        finally:
            await asyncio.to_thread(self._cleanup_staged, staged)

    @staticmethod
    def _tx_labels(transaction_id: str) -> dict[str, str]:
        return {
            "io.dicepp.upgrade-transaction": transaction_id,
            "io.dicepp.upgrade-role": "runtime",
        }

    async def create_target_runtimes(
        self,
        current: dict[str, Any],
        staged: dict[str, Any],
        transaction_id: str,
    ) -> dict[str, Any]:
        """Takeover: create target Bot/Dashboard with labels and restart=no.

        Containers are created but not started; the coordinator starts them
        according to the captured original running state after migration.
        """
        created: list[str] = []
        for role in ("bot", "dashboard"):
            await self.executor._replace(
                current["containers"][role],
                staged["images"][role],
                extra_labels=self._tx_labels(transaction_id),
                restart_policy="no",
                start=False,
                expected_container_id=current["containers"][role][
                    "container_id"
                ],
                expected_transaction_id=transaction_id,
            )
            created.append(role)
        return {"roles": created}

    async def verify_target_manager_identity(
        self,
        request: dict[str, Any],
        *,
        allow_restored_restart_policy: bool = False,
    ) -> None:
        """Authorize the exact target Manager before shared-state mutation.

        During takeover the transaction requires ``restart=no``.  The source
        helper restores the captured target-side policy immediately before it
        writes ``result=target-committed``; only recovery holding that bound
        terminal result may opt into accepting the restored policy.
        """
        if self.handoff is None:
            raise UpgradeCompatibilityError(
                "Linux Manager handoff executor is unavailable",
                code="manager_handoff_unavailable",
            )
        try:
            identity = await self.handoff.inspect_current(socket.gethostname())
        except DockerRuntimeError as exc:
            raise UpgradeCompatibilityError(
                "Target Manager self identity cannot be verified",
                code="target_manager_identity_invalid",
            ) from exc
        manager = request["manager"]
        transaction_label = request["labels"]["transaction"]
        role_label = request["labels"]["role"]
        expected_restart_policy = (
            request["restart_policies"]["manager"]
            if allow_restored_restart_policy
            else "no"
        )
        if (
            identity.container_id == manager["container_id"]
            or identity.name != manager["name"]
            or identity.image_id != request["target_manager_image_id"]
            or identity.running is not True
            or identity.restart_policy != expected_restart_policy
            or identity.compose_project != request["compose_project"]
            or identity.compose_service != "manager"
            or identity.labels.get(transaction_label) != request["transaction_id"]
            or identity.labels.get(role_label) != "manager"
        ):
            raise UpgradeCompatibilityError(
                "Target Manager Docker identity does not match the handoff request",
                code="target_manager_identity_invalid",
            )

    async def verify_target_container_images(
        self, request: dict[str, Any]
    ) -> None:
        """Fail closed unless the running target containers match the request.

        Resolves every container carrying this transaction's
        ``io.dicepp.upgrade-transaction`` label — the target Manager (created
        by the Updater, role ``manager``), the target Bot/Dashboard (created
        by :meth:`create_target_runtimes`, role ``runtime``) and the Updater
        itself (role ``updater``, skipped) — and verifies each exact Image ID
        against the request's ``target_images`` / ``target_manager_image_id``.
        The captured source ids cannot be used here: they are deleted when
        the targets replace them.  The coordinator's takeover hard health
        calls this before writing ``decision=commit``; a missing container,
        an unexpected role or any mismatch raises
        :class:`UpgradeCompatibilityError`.
        """
        if self.handoff is None:
            raise UpgradeCompatibilityError(
                "Linux Manager handoff executor is unavailable",
                code="manager_handoff_unavailable",
            )
        transaction_label = request["labels"]["transaction"]
        role_label = request["labels"]["role"]
        expected = {
            "manager": request["target_manager_image_id"],
            "bot": request["target_images"]["bot"],
            "dashboard": request["target_images"]["dashboard"],
        }
        verified: set[str] = set()
        ids = await self.handoff.list_by_label(
            transaction_label, request["transaction_id"]
        )
        for container_id in ids:
            identity = await self.handoff.inspect(container_id)
            role = identity.labels.get(role_label)
            if role == "updater":
                continue
            if role == "manager":
                key = "manager"
            elif role == "runtime":
                key = identity.compose_service
                if key not in expected:
                    raise UpgradeCompatibilityError(
                        f"Target runtime container {container_id} has an "
                        f"unknown compose service",
                        code="target_image_mismatch",
                    )
            else:
                raise UpgradeCompatibilityError(
                    f"Container {container_id} has an unknown transaction "
                    f"role in the handoff window",
                    code="target_image_mismatch",
                )
            if key in verified:
                raise UpgradeCompatibilityError(
                    f"Duplicate target {key} container in the handoff window",
                    code="target_image_mismatch",
                )
            if identity.image_id != expected[key]:
                raise UpgradeCompatibilityError(
                    f"Running {key} container image does not match the "
                    f"staged target image",
                    code="target_image_mismatch",
                )
            verified.add(key)
        if verified != set(expected):
            raise UpgradeCompatibilityError(
                f"Running target containers are incomplete; missing "
                f"{sorted(set(expected) - verified)}",
                code="target_image_mismatch",
            )

    async def restore_source_runtimes(
        self,
        current: dict[str, Any],
        *,
        transaction_id: str | None = None,
    ) -> dict[str, Any]:
        """Rollback: rebuild source Bot/Dashboard with their original policy.

        ``transaction_id`` authorizes replacing containers that carry this
        transaction's label (created by the target Manager during takeover).
        """
        return await self.executor.restore_images(
            current, transaction_id=transaction_id
        )

    async def restore_runtime_policies(self, detail: dict[str, Any]) -> None:
        """Commit convergence: restore target runtime policy and running state.

        This is deliberately owned by the target Manager.  The external
        orchestrator only starts the target Manager; after an authoritative
        ``target-committed`` result, this method converges the exact
        transaction-bound Bot and Dashboard to the state captured in the
        request.  All identities are validated before the first mutation.
        """
        if self.handoff is None:
            raise UpgradeCompatibilityError(
                "Linux Manager handoff executor is unavailable",
                code="manager_handoff_unavailable",
            )
        staged = detail.get("platform_staged")
        request = staged.get("request") if isinstance(staged, dict) else None
        if not isinstance(request, dict):
            raise UpgradeCompatibilityError(
                "Linux handoff request is unavailable for policy restore"
            )
        transaction_id = str(detail.get("transaction_id") or "")
        if not transaction_id or transaction_id != request.get("transaction_id"):
            raise UpgradeCompatibilityError(
                "Linux handoff transaction does not match the bound request",
                code="target_runtime_identity_invalid",
            )

        transaction_label = request["labels"]["transaction"]
        role_label = request["labels"]["role"]
        policies = request["restart_policies"]
        original_running = request["original_running"]
        expected_names = {"bot": "dicepp", "dashboard": "dicepp-dashboard"}
        runtime_identities: dict[str, Any] = {}

        ids = await self.handoff.list_by_label(
            transaction_label, transaction_id
        )
        for container_id in ids:
            identity = await self.handoff.inspect(container_id)
            handoff_role = identity.labels.get(role_label)
            if handoff_role in {"manager", "updater"}:
                continue
            if handoff_role != "runtime":
                raise UpgradeCompatibilityError(
                    f"Container {container_id} has an unknown transaction role",
                    code="target_runtime_identity_invalid",
                )
            role = identity.compose_service
            if role not in expected_names or role in runtime_identities:
                raise UpgradeCompatibilityError(
                    f"Target runtime {container_id} has an unknown or duplicate role",
                    code="target_runtime_identity_invalid",
                )
            allowed_policies = {"no", policies[role]}
            if (
                identity.container_id != container_id
                or identity.name != expected_names[role]
                or identity.image_id != request["target_images"][role]
                or identity.compose_project != request["compose_project"]
                or identity.labels.get(transaction_label) != transaction_id
                or identity.restart_policy not in allowed_policies
                or (not original_running[role] and identity.running)
            ):
                raise UpgradeCompatibilityError(
                    f"Target {role} runtime identity does not match the handoff request",
                    code="target_runtime_identity_invalid",
                )
            runtime_identities[role] = identity

        missing = {"bot", "dashboard"} - set(runtime_identities)
        if missing:
            raise UpgradeCompatibilityError(
                f"Target runtime containers are incomplete; missing {sorted(missing)}",
                code="target_runtime_identity_invalid",
            )

        # Mutate only after every transaction-bound runtime has passed the
        # identity, image, project, policy and original-state checks.
        for role in ("bot", "dashboard"):
            identity = runtime_identities[role]
            if identity.restart_policy != policies[role]:
                await self.handoff.set_restart_policy(
                    identity.container_id, policies[role]
                )
        for role in ("bot", "dashboard"):
            identity = runtime_identities[role]
            if original_running[role] and not identity.running:
                await self.handoff.start(identity.container_id)

        # Re-inspect after convergence so a failed Docker mutation cannot be
        # mistaken for a completed commit.
        for role in ("bot", "dashboard"):
            expected = runtime_identities[role]
            actual = await self.handoff.inspect(expected.container_id)
            if (
                actual.container_id != expected.container_id
                or actual.name != expected_names[role]
                or actual.image_id != request["target_images"][role]
                or actual.compose_project != request["compose_project"]
                or actual.compose_service != role
                or actual.labels.get(transaction_label) != transaction_id
                or actual.labels.get(role_label) != "runtime"
                or actual.restart_policy != policies[role]
                or actual.running is not bool(original_running[role])
            ):
                raise UpgradeCompatibilityError(
                    f"Target {role} runtime did not converge to the committed state",
                    code="target_runtime_convergence_failed",
                )

    async def update_current_aliases(
        self,
        current: dict[str, Any],
        target_image_ids: dict[str, str],
    ) -> None:
        """Move both local aliases to the target IDs, verifying each move.

        Any failure restores both aliases to the source IDs and re-verifies
        them before re-raising, so a half-moved alias never survives a failed
        takeover.
        """
        if self.handoff is None:
            raise UpgradeCompatibilityError(
                "Linux Manager handoff executor is unavailable",
                code="manager_handoff_unavailable",
            )
        aliases = self._validated_current_aliases(current)
        moved: list[str] = []
        try:
            for role, alias in aliases.items():
                target_id = target_image_ids[
                    "bot" if role == "bot" else "dashboard"
                ]
                repo, tag = alias["name"].rsplit(":", 1)
                await self.handoff.tag_image(target_id, repo, tag)
                payload = await self.executor.inspect_tag(alias["name"])
                if payload.get("Id") != target_id:
                    raise UpgradeCompatibilityError(
                        f"local alias {alias['name']} did not move to the target",
                        code="current_alias_update_failed",
                    )
                moved.append(role)
        except Exception:
            for role, alias in aliases.items():
                repo, tag = alias["name"].rsplit(":", 1)
                try:
                    await self.handoff.tag_image(alias["image_id"], repo, tag)
                except DockerRuntimeError:
                    pass
            for role, alias in aliases.items():
                payload = await self.executor.inspect_tag(alias["name"])
                if payload.get("Id") != alias["image_id"]:
                    raise UpgradeCompatibilityError(
                        "local aliases could not be restored to the source; "
                        "manual recovery required",
                        code="current_alias_restore_failed",
                    ) from None
            raise

    @staticmethod
    def _validated_current_aliases(
        current: dict[str, Any],
    ) -> dict[str, dict[str, str]]:
        aliases = current.get("current_aliases")
        if not isinstance(aliases, dict) or set(aliases) != set(
            CURRENT_ALIAS_NAMES
        ):
            raise UpgradeCompatibilityError(
                "managed current alias contract is invalid",
                code="current_alias_invalid",
            )
        for role, expected_name in CURRENT_ALIAS_NAMES.items():
            alias = aliases.get(role)
            if (
                not isinstance(alias, dict)
                or alias.get("name") != expected_name
                or not isinstance(alias.get("image_id"), str)
                or not re.fullmatch(r"sha256:[0-9a-f]{64}", alias["image_id"])
            ):
                raise UpgradeCompatibilityError(
                    f"managed {role} current alias contract is invalid",
                    code="current_alias_invalid",
                )
        return aliases

    async def restore_current_aliases(self, current: dict[str, Any]) -> None:
        """Point both aliases back at the captured source Image IDs."""
        await self.update_current_aliases(
            current,
            {
                "bot": current["containers"]["bot"]["image_id"],
                "dashboard": current["containers"]["dashboard"]["image_id"],
            },
        )

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


from .windows_upgrade import SimpleWindowsVelopackUpgradeAdapter  # noqa: E402


class UpgradeCoordinator(LinuxHandoffCoordinator):
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
        runtime_support = service.maintenance_runtime_support or getattr(
            archive_coordinator,
            "runtime_support",
            None,
        )
        if runtime_support is None:
            runtime_support = MaintenanceRuntimeSupport(
                layout=layout,
                service=service,
                control_probe=getattr(archive_coordinator, "control_probe", None),
            )
            service.maintenance_runtime_support = runtime_support
        archive_housekeeping = service.archive_housekeeping or getattr(
            archive_coordinator,
            "housekeeping",
            None,
        )
        if archive_housekeeping is None:
            archive_housekeeping = ArchiveHousekeeping(layout=layout, store=self.store)
            service.archive_housekeeping = archive_housekeeping
        self.runtime_support = runtime_support
        self.archive_housekeeping = archive_housekeeping
        self.release_manager = release_manager
        self.platform_adapter = platform_adapter
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.fault_hook = fault_hook
        self._api_ready = asyncio.Event()
        self._handoff_status: dict[str, Any] | None = None
        self._init_linux_handoff_coordinator(
            upgrade_error=UpgradeError,
            upgrade_compatibility_error=UpgradeCompatibilityError,
        )
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

    def should_quiesce_runtime_on_shutdown(self) -> bool:
        """Return whether durable recovery state requires Runtime shutdown.

        The startup maintenance gate protects recovery from user operations;
        it does not itself imply that Runtime must stop when Manager exits.
        Explicit policy wins.  The narrow phase fallback preserves the
        Windows simple-upgrade contract for rc20-rc22 journals written before
        the policy field existed.
        """
        for journal in self.store.list_recoverable_journals():
            if journal.get("kind") != UPGRADE_JOURNAL_KIND:
                continue
            detail = journal.get("detail")
            if not isinstance(detail, dict):
                continue
            if SHUTDOWN_RUNTIME_POLICY_FIELD in detail:
                if (
                    detail.get(SHUTDOWN_RUNTIME_POLICY_FIELD)
                    == SHUTDOWN_RUNTIME_QUIESCE
                ):
                    return True
                continue
            if detail.get("platform_protocol") != "windows-simple-v1":
                continue
            phase = str(journal.get("phase") or detail.get("phase") or "")
            if phase in _LEGACY_WINDOWS_SHUTDOWN_QUIESCE_PHASES:
                return True
            if phase in {"rolling_back", "rollback_failed"}:
                manual = detail.get("manual_restore")
                if isinstance(manual, dict) and manual.get("requested") is True:
                    return True
        return False

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
            "automatic_rollback": self.platform_adapter.platform != "windows",
            **(
                {
                    "manual_recovery_entry": "DicePP-Recover.cmd",
                    "recovery_scope": "program_data_runtime",
                }
                if self.platform_adapter.platform == "windows"
                else {}
            ),
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
            "runtime_state_captured": False,
            "commit_point": "not_started",
            "rolled_back": False,
            "rollback_status": "not_started",
            SHUTDOWN_RUNTIME_POLICY_FIELD: SHUTDOWN_RUNTIME_KEEP,
        }
        protocol = getattr(self.platform_adapter, "protocol", None)
        if isinstance(protocol, str) and protocol:
            detail["platform_protocol"] = protocol
        operation.transition("running", detail=detail)
        self.store.save(operation)
        self._journal(operation, detail)
        try:
            preflight = await self.platform_adapter.preflight(package)
            detail["preflight"] = preflight
            self._phase(operation, detail, "pre_upgrade_archive", 15)
            baseline, control_gate = await self.runtime_support.capture_control_baseline()
            detail["control_heartbeat_baseline"] = baseline
            detail["control_gate"] = control_gate
            with self._maintenance_context(maintenance_lease) as maintenance:
                original, _ = await self.runtime_support.quiesce(
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
                # Persist the recovery path before preparing scripts/metadata.  If
                # preparation is interrupted, startup recovery can now remove the
                # exact transaction instead of leaving an upgrade-blocking orphan.
                self._journal(operation, detail)
                prepare_recovery = getattr(
                    self.platform_adapter,
                    "prepare_recovery",
                    None,
                )
                if callable(prepare_recovery):
                    staged["current"] = current
                    staged["operation_id"] = operation.operation_id
                    # The request contract requires a non-empty source_version
                    # (the running source Manager's own version); the recovery
                    # identity gates (is_source/is_target) depend on it.  The
                    # adapter must report it through capture_current; never
                    # substitute the code version here, or an adapter that
                    # stopped capturing it would silently bind a bogus source.
                    # Fail closed: no request is written and the transaction
                    # rolls back (commit_point is still not_started).
                    if protocol == "linux-manager-handoff-v1":
                        source_version = current.get("source_version")
                        if not isinstance(source_version, str) or not source_version:
                            raise UpgradeError(
                                "Linux Manager handoff requires the running "
                                "source version; capture_current did not "
                                "report a non-empty source_version",
                                code="source_version_unavailable",
                            )
                        source_version = str(source_version)
                    else:
                        source_version = str(current.get("source_version") or "")
                    staged = await prepare_recovery(
                        staged,
                        transaction_id=transaction_id,
                        source_version=source_version,
                        target_version=package.version,
                        pre_upgrade_filename=str(detail["pre_upgrade_filename"]),
                        original_running=list(original),
                    )
                    detail["platform_staged"] = staged
                    self._journal(operation, detail)
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
                    phase = (
                        "awaiting_manager_handoff"
                        if switch.get("shutdown_required") is False
                        else "awaiting_windows_restart"
                    )
                    detail["phase"] = phase
                    detail["progress"] = 55
                    self._journal(
                        operation,
                        detail,
                        phase=phase,
                    )
                    operation.transition(
                        "running",
                        message=(
                            "Waiting for the Manager handoff transaction"
                            if phase == "awaiting_manager_handoff"
                            else "Velopack is applying the Windows update"
                        ),
                        detail=detail,
                    )
                    self.store.save(operation)
                    if switch.get("shutdown_required") is not False:
                        asyncio.get_running_loop().call_later(
                            0.25,
                            self.service.request_shutdown,
                            "windows_velopack_handoff",
                        )
                    return operation
                self._phase(operation, detail, "migration", 65)
                self._fault("migration")
                migrations = await asyncio.to_thread(
                    self.runtime_support.migrate_and_validate_schema
                )
                detail["migrations"] = migrations
                self._phase(operation, detail, "runtime_start", 75)
                await self.runtime_support.restart(maintenance, original)
                self._fault("runtime_start")
                self._phase(operation, detail, "health", 85)
                health = await self.runtime_support.hard_health(
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
            self.archive_housekeeping.apply_retention()
            operation.transition(
                "succeeded",
                message=f"Upgrade to {package.version} committed",
                detail=detail,
            )
            self.store.save(operation)
            self.store.retire_terminal_rollback_journals()
            self._retire_superseded_interrupted_upgrades()
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
        # ``prepare_windows_handoff_only`` predates Linux Manager handoff.
        # Keep the keyword compatible, but treat it as the startup prepare
        # phase for every handoff whose recovery depends on the Manager API.
        self._retire_superseded_interrupted_upgrades()
        recovered: list[dict[str, Any]] = []
        runtime_state_owned: set[str] = set()
        # The startup maintenance gate blocks user-submitted operations while
        # recovery is pending; recovery itself raises that gate below and must
        # stay exempt from it, otherwise it deadlocks against its own gate and
        # misrecords the conflict as a rollback failure.
        recovery_allowed = allow_startup_recovery
        for journal in self.store.list_recoverable_journals():
            if journal.get("kind") != UPGRADE_JOURNAL_KIND:
                continue
            detail = dict(journal.get("detail") or {})
            transaction_id = str(journal["transaction_id"])
            if (
                isinstance(self.platform_adapter, SimpleWindowsVelopackUpgradeAdapter)
                and detail.get("platform_protocol") == "windows-simple-v1"
                and detail.get("runtime_state_captured") is True
            ):
                runtime_state_owned.add(transaction_id)
            operation = (
                self.store.get(str(journal.get("operation_id")))
                if journal.get("operation_id")
                else None
            )
            if operation is None:
                operation = self.new_operation()
            if isinstance(
                self.platform_adapter,
                SimpleWindowsVelopackUpgradeAdapter,
            ):
                simple_result = await self._recover_simple_windows_handoff(
                    journal,
                    operation,
                    detail,
                    prepare_only=prepare_windows_handoff_only,
                )
                if simple_result is not None:
                    recovered.append(
                        {"transaction_id": transaction_id, **simple_result}
                    )
                    continue
            if detail.get("platform_protocol") == "linux-manager-handoff-v1":
                # Only a transaction whose Updater was actually created is a
                # Manager handoff to resume.  A journal interrupted before the
                # switch — no platform_staged, no transaction dir, or no
                # durable program_switch result — falls through to the generic
                # auto-abort path below; routing it here would demand manual
                # recovery for a switch that never started, while the generic
                # path cleans staging and best-effort restarts the quiesced
                # runtimes.
                linux_staged = detail.get("platform_staged")
                linux_tx_dir = (
                    linux_staged.get("transaction_dir")
                    if isinstance(linux_staged, dict)
                    else None
                )
                linux_switch = detail.get("program_switch")
                updater_created = (
                    isinstance(linux_switch, dict)
                    and linux_switch.get("handoff_required") is True
                )
                if (
                    isinstance(linux_tx_dir, str)
                    and linux_tx_dir
                    and updater_created
                ):
                    runtime_state_owned.add(transaction_id)
                    if prepare_windows_handoff_only:
                        # Target takeover waits for a Bot heartbeat reported
                        # through this Manager's control WebSocket.  Running it
                        # inside ASGI lifespan startup would wait on an endpoint
                        # that cannot listen until lifespan yields.
                        self.service.set_startup_maintenance_gate(True)
                        recovered.append({
                            "transaction_id": transaction_id,
                            "action": "awaiting_api_bind",
                        })
                        continue
                    linux_result = await self._recover_linux_manager_handoff(
                        journal,
                        operation,
                        detail,
                    )
                    if linux_result is not None:
                        recovered.append(
                            {"transaction_id": transaction_id, **linux_result}
                        )
                        continue
            if is_terminal_rollback_failure(journal):
                recovered.append({
                    "transaction_id": transaction_id,
                    "action": "rollback_failed",
                    "manual_recovery_required": True,
                })
                continue
            if detail.get("commit_point") == "not_started":
                # No program/data switch occurred, so abort does not depend on
                # the target package cache still being available.
                self.archive_housekeeping.cleanup_inprogress()
                cleanup_error = await self._cleanup_platform_staging(detail)
                restart_error = await self.runtime_support.best_effort_restart(
                    _string_list(detail.get("original_running")),
                    allow_startup_recovery=recovery_allowed,
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
                if (
                    isinstance(
                        self.platform_adapter,
                        SimpleWindowsVelopackUpgradeAdapter,
                    )
                    and detail.get("commit_point") == "health_passed"
                ):
                    finalized = await self._finalize_recovered_commit(
                        operation,
                        None,
                        detail,
                        transaction_id=transaction_id,
                        package_error=str(exc) or type(exc).__name__,
                    )
                    recovered.append({
                        "transaction_id": transaction_id,
                        "action": (
                            "finalized_with_package_warning"
                            if finalized
                            else "commit_cleanup_pending"
                        ),
                    })
                    continue
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
                finalized = await self._finalize_recovered_commit(
                    operation,
                    package,
                    detail,
                    transaction_id=transaction_id,
                )
                recovered.append(
                    {
                        "transaction_id": transaction_id,
                        "action": (
                            "finalized" if finalized else "commit_cleanup_pending"
                        ),
                    }
                )
                continue
            rollback = await self._rollback(
                operation,
                package,
                detail,
                allow_startup_recovery=recovery_allowed,
            )
            rollback_succeeded = rollback.get("succeeded") is True
            recovery_error = (
                None
                if rollback_succeeded
                else str(
                    rollback.get("error")
                    or "Interrupted upgrade rollback failed"
                )
            )
            operation.transition(
                "failed",
                message=(
                    "Interrupted upgrade automatically rolled back"
                    if rollback_succeeded
                    else "Interrupted upgrade rollback failed; "
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
                        {
                            "recovery_error": recovery_error,
                            "manual_recovery_required": True,
                        }
                        if recovery_error is not None
                        else {}
                    ),
                },
            )
            self.store.save(operation)
            recovered.append(
                {
                    "transaction_id": transaction_id,
                    "action": (
                        "rolled_back" if rollback_succeeded else "rollback_failed"
                    ),
                    "result": rollback,
                    **(
                        {"manual_recovery_required": True}
                        if not rollback_succeeded
                        else {}
                    ),
                }
            )
        for result in recovered:
            if str(result.get("transaction_id") or "") in runtime_state_owned:
                result["owns_runtime_state"] = True
        return recovered

    async def _recover_simple_windows_handoff(
        self,
        journal: dict[str, Any],
        operation: ManagerOperation,
        detail: dict[str, Any],
        *,
        prepare_only: bool,
    ) -> dict[str, Any] | None:
        """Resume only the source-owned simple Windows recovery contract."""

        transaction_id = str(detail.get("transaction_id") or "")
        simple_protocol = detail.get("platform_protocol") == "windows-simple-v1"
        if not simple_protocol:
            # rc20 is a clean protocol boundary: old Guard journals have no
            # discriminator and are evidence only.  Never interpret any of
            # their markers, phases, or commit points as the simple protocol.
            return {"action": "ignored_legacy_windows_upgrade"}
        manual = self.platform_adapter.load_manual_restore_request(detail)
        persisted_rollback = detail.get("rollback_result")
        if (
            manual is None
            and detail.get("platform_protocol") == "windows-simple-v1"
            and detail.get("phase")
            in {"manual_data_restored", "manual_cleanup_failed"}
            and isinstance(persisted_rollback, dict)
            and persisted_rollback.get("succeeded") is True
        ):
            staged = detail.get("platform_staged")
            manual = {
                "source_version": (
                    staged.get("source_version")
                    if isinstance(staged, dict)
                    else ""
                )
            }
        if manual is not None:
            self.service.set_startup_maintenance_gate(True)
            source_version = str(manual.get("source_version") or "")
            actual_version = get_version()
            try:
                source_is_running = (
                    actual_version != "unknown"
                    and source_version
                    and Version(actual_version) == Version(source_version)
                )
            except Exception:
                source_is_running = False
            if not source_is_running:
                blocked_detail = {
                    **detail,
                    "phase": "manual_restore_blocked",
                    SHUTDOWN_RUNTIME_POLICY_FIELD: SHUTDOWN_RUNTIME_QUIESCE,
                    "manual_restore": {
                        "requested": True,
                        "program_directory_restored": False,
                        "data_runtime_restored": False,
                        "error": (
                            f"Running Manager version {actual_version!r} does not "
                            f"match recovery source {source_version!r}"
                        ),
                    },
                    "manual_recovery_required": True,
                }
                self._journal(
                    operation,
                    blocked_detail,
                    phase="manual_restore_blocked",
                    status="rollback_failed",
                )
                operation.transition(
                    "failed",
                    message=(
                        "Manual recovery marker belongs to another Manager version"
                    ),
                    detail=blocked_detail,
                )
                self.store.save(operation)
                return {
                    "action": "manual_restore_blocked",
                    "actual_version": actual_version,
                    "source_version": source_version,
                    "program_directory_restored": False,
                    "data_runtime_restored": False,
                    "manual_recovery_required": True,
                }
            if detail.get("phase") == "manual_restore_failed":
                return {
                    "action": "manual_restore_failed",
                    "program_directory_restored": True,
                    "data_runtime_restored": False,
                    "manual_recovery_required": True,
                }
            if (
                detail.get("phase")
                in {"manual_data_restored", "manual_cleanup_failed"}
                and isinstance(persisted_rollback, dict)
                and persisted_rollback.get("succeeded") is True
            ):
                # Data and Runtime restoration already completed before the
                # prior process died.  Do not replay destructive restoration;
                # resume only recovery-material cleanup and terminal recording.
                detail[SHUTDOWN_RUNTIME_POLICY_FIELD] = SHUTDOWN_RUNTIME_KEEP
                rollback = dict(persisted_rollback)
            else:
                detail.update(
                    {
                        "phase": "manual_data_restore",
                        SHUTDOWN_RUNTIME_POLICY_FIELD: SHUTDOWN_RUNTIME_QUIESCE,
                        "manual_restore": {
                            "requested": True,
                            "program_directory_restored": True,
                            "data_runtime_restored": False,
                        },
                    }
                )
                self._journal(
                    operation,
                    detail,
                    phase="manual_data_restore",
                    status="interrupted",
                )
                rollback = await self._rollback(
                    operation,
                    None,
                    detail,
                    program_already_restored=True,
                    allow_startup_recovery=True,
                )
            if rollback.get("succeeded") is not True:
                failed_detail = {
                    **detail,
                    "phase": "manual_restore_failed",
                    "manual_restore": {
                        "requested": True,
                        "program_directory_restored": True,
                        "data_runtime_restored": False,
                        "error": str(
                            rollback.get("error")
                            or "Pre-upgrade data or Runtime restoration failed"
                        ),
                    },
                    "rollback_result": rollback,
                    "manual_recovery_required": True,
                }
                self._journal(
                    operation,
                    failed_detail,
                    phase="manual_restore_failed",
                    status="rollback_failed",
                )
                operation.transition(
                    "failed",
                    message=(
                        "Previous program directory was restored, but its data "
                        "or Runtime state could not be restored"
                    ),
                    detail=failed_detail,
                )
                self.store.save(operation)
                return {
                    "action": "manual_restore_failed",
                    "result": rollback,
                    "program_directory_restored": True,
                    "data_runtime_restored": False,
                    "manual_recovery_required": True,
                }

            cleanup_error = await self.runtime_support.best_effort_restore_state(
                _string_list(detail.get("original_running")),
                allow_startup_recovery=True,
            )
            if cleanup_error is not None:
                cleanup_error = (
                    "Restored Runtime state could not be reasserted before cleanup: "
                    f"{cleanup_error}"
                )
            else:
                cleanup_error = await self.platform_adapter.finish_manual_restore(
                    dict(detail.get("platform_staged") or {}),
                    transaction_id,
                )
            if cleanup_error is not None:
                cleanup_detail = {
                    **detail,
                    "phase": "manual_cleanup_failed",
                    SHUTDOWN_RUNTIME_POLICY_FIELD: SHUTDOWN_RUNTIME_KEEP,
                    "manual_restore": {
                        "requested": True,
                        "program_directory_restored": True,
                        "data_runtime_restored": True,
                        "cleanup_warning": cleanup_error,
                    },
                    "rollback_result": rollback,
                    "rolled_back": True,
                    "rollback_status": "succeeded",
                }
                self._journal(
                    operation,
                    cleanup_detail,
                    phase="manual_cleanup_failed",
                    status="interrupted",
                )
                operation.transition(
                    "interrupted",
                    message=(
                        "Manual restore succeeded; recovery-material cleanup "
                        "will be retried"
                    ),
                    detail=cleanup_detail,
                )
                self.store.save(operation)
                return {
                    "action": "manual_cleanup_pending",
                    "result": rollback,
                    "cleanup_warning": cleanup_error,
                }
            restored_detail = {
                **detail,
                "phase": "manual_restored",
                SHUTDOWN_RUNTIME_POLICY_FIELD: SHUTDOWN_RUNTIME_KEEP,
                "manual_restore": {
                    "requested": True,
                    "program_directory_restored": True,
                    "data_runtime_restored": True,
                },
                "rollback_result": rollback,
                "rolled_back": True,
                "rollback_status": "succeeded",
            }
            self._journal(
                operation,
                restored_detail,
                phase="manual_restored",
                status="rolled_back",
            )
            operation.transition(
                "failed",
                message="Upgrade was manually restored to the previous version",
                detail=restored_detail,
            )
            self.store.save(operation)
            self.service.set_startup_maintenance_gate(False)
            return {
                "action": "manual_restored",
                "result": rollback,
                "cleanup_warning": None,
            }

        if simple_protocol and detail.get("commit_point") == "health_passed":
            # The target health decision is durable.  Let the generic recovery
            # finalizer retry commit/cleanup idempotently; this is rc20 state,
            # not a legacy Guard phase.
            self.service.set_startup_maintenance_gate(True)
            return None
        if (
            detail.get("commit_point") == "not_started"
            and detail.get("platform_protocol") == "windows-simple-v1"
        ):
            # rc20 staging/preparation was interrupted before Velopack started.
            # Generic recovery owns the exact transaction cleanup and Runtime
            # state restoration for this pre-switch state.
            return None
        if (
            simple_protocol
            and detail.get("commit_point") == "program_switch_started"
            and detail.get("phase") != "target_health_failed"
        ):
            actual_version = get_version()
            target_version = str(detail.get("target_version") or "")
            current = detail.get("platform_current")
            staged = detail.get("platform_staged")
            source_version = str(
                (
                    current.get("source_version")
                    if isinstance(current, dict)
                    else None
                )
                or (
                    staged.get("source_version")
                    if isinstance(staged, dict)
                    else None
                )
                or ""
            )
            try:
                running_target = (
                    actual_version != "unknown"
                    and target_version
                    and Version(actual_version) == Version(target_version)
                )
                running_source = (
                    actual_version != "unknown"
                    and source_version
                    and Version(actual_version) == Version(source_version)
                )
            except Exception:
                running_target = running_source = False
            if running_target:
                # Velopack completed before the source Manager could persist its
                # handoff phase.  Normalize durably and use the ordinary target
                # migration/health/commit path below.
                detail["phase"] = "awaiting_windows_restart"
                self._journal(
                    operation,
                    detail,
                    phase="awaiting_windows_restart",
                    status="interrupted",
                )
            elif running_source:
                # Velopack did not replace the program.  No data migration has
                # started, so downgrade to the package-independent pre-switch
                # abort path instead of pretending program rollback occurred.
                detail["commit_point"] = "not_started"
                detail["phase"] = "switch_aborted_on_source"
                self._journal(
                    operation,
                    detail,
                    phase="switch_aborted_on_source",
                    status="interrupted",
                )
                return None
            else:
                self.service.set_startup_maintenance_gate(True)
                blocked_detail = {
                    **detail,
                    "phase": "switch_identity_unknown",
                    SHUTDOWN_RUNTIME_POLICY_FIELD: SHUTDOWN_RUNTIME_QUIESCE,
                    "actual_version": actual_version,
                    "source_version": source_version,
                    "manual_recovery_required": True,
                }
                self._journal(
                    operation,
                    blocked_detail,
                    phase="switch_identity_unknown",
                    status="rollback_failed",
                )
                operation.transition(
                    "failed",
                    message=(
                        "Interrupted Windows switch cannot be classified; "
                        "recovery material was preserved"
                    ),
                    detail=blocked_detail,
                )
                self.store.save(operation)
                return {
                    "action": "manual_recovery_required",
                    "error": "windows_switch_identity_unknown",
                    "program_directory_restored": False,
                    "manual_recovery_required": True,
                }
        if detail.get("phase") not in {
            "awaiting_windows_restart",
            "target_health_failed",
        }:
            return None
        if detail.get("phase") == "target_health_failed":
            self.service.set_startup_maintenance_gate(True)
            return {
                "action": "manual_recovery_required",
                "program_directory_restored": False,
                "manual_recovery_required": True,
            }
        self.service.set_startup_maintenance_gate(True)
        if prepare_only:
            return {"action": "awaiting_api_bind"}
        try:
            actual_version = get_version()
            target_version = str(detail.get("target_version") or "")
            if (
                actual_version == "unknown"
                or not target_version
                or Version(actual_version) != Version(target_version)
            ):
                raise UpgradeCompatibilityError(
                    f"Updated Manager version {actual_version!r} does not match "
                    f"target {target_version!r}"
                )
            package: VerifiedUpgradePackage | None = None
            package_error: str | None = None
            release_snapshot = detail.get("release_snapshot")
            if not isinstance(release_snapshot, dict):
                package_error = "Interrupted upgrade has no durable Release snapshot"
            else:
                try:
                    package = self._package_from_release(
                        target_version,
                        release_snapshot,
                    )
                except Exception as package_exc:
                    package_error = str(package_exc) or type(package_exc).__name__
            with self._maintenance_context(
                None,
                timeout=1,
                allow_startup_recovery=True,
            ) as maintenance:
                migrations = await asyncio.to_thread(
                    self.runtime_support.migrate_and_validate_schema
                )
                original = _string_list(detail.get("original_running"))
                await self.runtime_support.restart(maintenance, original)
                health = await self.runtime_support.hard_health(
                    original,
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
        except Exception as exc:
            failed_detail = {
                **detail,
                "phase": "target_health_failed",
                SHUTDOWN_RUNTIME_POLICY_FIELD: SHUTDOWN_RUNTIME_QUIESCE,
                "new_version_health_error": str(exc) or type(exc).__name__,
                "target_runtime_stopped": False,
                "manual_recovery_required": True,
                "manual_restore": {
                    "requested": False,
                    "program_directory_restored": False,
                    "data_runtime_restored": False,
                },
            }
            # Persist the shutdown requirement before the first stop attempt.
            # If Manager exits during that attempt, lifespan shutdown can retry.
            self._journal(
                operation,
                failed_detail,
                phase="target_health_failed",
                status="rollback_failed",
            )
            target_runtime_stop_error = None
            try:
                with self._maintenance_context(
                    None,
                    timeout=1,
                    allow_startup_recovery=True,
                ) as maintenance:
                    await self.runtime_support.quiesce(maintenance)
            except Exception as stop_exc:
                target_runtime_stop_error = (
                    str(stop_exc) or type(stop_exc).__name__
                )
            failed_detail["target_runtime_stopped"] = (
                target_runtime_stop_error is None
            )
            if target_runtime_stop_error is not None:
                failed_detail["target_runtime_stop_error"] = (
                    target_runtime_stop_error
                )
            self._journal(
                operation,
                failed_detail,
                phase="target_health_failed",
                status="rollback_failed",
            )
            operation.transition(
                "failed",
                message=(
                    "Updated Windows program did not pass local health checks; "
                    "run DicePP-Recover.cmd after closing DicePP"
                ),
                detail=failed_detail,
            )
            self.store.save(operation)
            return {
                "action": "manual_recovery_required",
                "error": failed_detail["new_version_health_error"],
                "program_directory_restored": False,
                "manual_recovery_required": True,
            }

        # Once health_passed is durable, cleanup/commit persistence failures must
        # never be reclassified as target-health failures.  A crash or exception
        # here leaves the healthy journal recoverable and the commit is retried
        # idempotently on the next startup.
        finalized = await self._finalize_recovered_commit(
            operation,
            package,
            detail,
            transaction_id=transaction_id,
            package_error=package_error,
        )
        return {
            "action": "committed" if finalized else "commit_cleanup_pending"
        }

    def mark_api_ready(self) -> None:
        self._api_ready.set()

    async def wait_api_ready(self) -> None:
        await self._api_ready.wait()

    def handoff_health(self) -> dict[str, Any] | None:
        return dict(self._handoff_status) if self._handoff_status is not None else None

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
            restart_error = await self.runtime_support.best_effort_restart(
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
            await self.runtime_support.capture_control_baseline()
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
                await self.runtime_support.quiesce(maintenance)
                if program_already_restored:
                    program = {"already_restored_by_user": True}
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
                    self.runtime_support.migrate_and_validate_schema
                )
                await self.runtime_support.restart(maintenance, original)
                health = await self.runtime_support.hard_health(
                    original,
                    control_baseline=rollback_baseline,
                    control_gate=rollback_control_gate,
                    control_failure_is_warning=True,
                )
        except Exception as exc:
            cleanup_error = None
            if not program_already_restored:
                cleanup_error = await self._cleanup_platform_staging(detail)
            result = {
                "succeeded": False,
                "error": str(exc) or type(exc).__name__,
                "staging_cleanup_error": cleanup_error,
                **(
                    {
                        "staging_cleanup_skipped": (
                            "manual_program_directory_already_restored"
                        )
                    }
                    if program_already_restored
                    else {}
                ),
            }
            self._journal(
                operation,
                {**detail, "rollback_result": result},
                phase="rollback_failed",
                status="rollback_failed",
            )
            return result
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
        try:
            self.archive_housekeeping.apply_retention()
        except Exception as exc:
            result["warnings"] = [
                "Rollback retention cleanup failed: "
                f"{str(exc) or type(exc).__name__}"
            ]
        journal_phase = (
            "manual_data_restored" if program_already_restored else "rolled_back"
        )
        if program_already_restored:
            detail[SHUTDOWN_RUNTIME_POLICY_FIELD] = SHUTDOWN_RUNTIME_KEEP
        self._journal(
            operation,
            {**detail, "phase": journal_phase, "rollback_result": result},
            phase=journal_phase,
            status="interrupted" if program_already_restored else "rolled_back",
        )
        return result

    async def _finalize_recovered_commit(
        self,
        operation: ManagerOperation,
        package: VerifiedUpgradePackage | None,
        detail: dict[str, Any],
        *,
        transaction_id: str,
        package_error: str | None = None,
    ) -> bool:
        """Idempotently finalize a platform commit after durable target health."""

        simple_windows = isinstance(
            self.platform_adapter,
            SimpleWindowsVelopackUpgradeAdapter,
        )
        if simple_windows:
            restart_error = await self.runtime_support.best_effort_restore_state(
                _string_list(detail.get("original_running")),
                allow_startup_recovery=True,
            )
            if restart_error is not None:
                detail["platform_commit"] = {
                    "status": "cleanup_pending",
                    "recovery_material_removed": False,
                    "warnings": [
                        "Target Runtime state could not be reasserted before cleanup: "
                        f"{restart_error}"
                    ],
                }
            elif package is None:
                cleanup_error = await self._cleanup_platform_staging(detail)
                warnings = [
                    "Target package was unavailable during post-health finalization: "
                    f"{package_error or 'unknown package error'}"
                ]
                if cleanup_error is not None:
                    warnings.append(cleanup_error)
                detail["platform_commit"] = {
                    "status": "committed",
                    "recovery_material_removed": cleanup_error is None,
                    "warnings": warnings,
                }
            else:
                detail["platform_commit"] = await self.platform_adapter.commit(
                    package,
                    current=dict(detail.get("platform_current") or {}),
                    staged=dict(detail.get("platform_staged") or {}),
                    transaction_id=transaction_id,
                )
        else:
            if package is None:
                raise UpgradeCompatibilityError(
                    "Target package is required to finalize this platform"
                )
            detail["platform_commit"] = await self.platform_adapter.commit(
                package,
                current=dict(detail.get("platform_current") or {}),
                staged=dict(detail.get("platform_staged") or {}),
                transaction_id=transaction_id,
            )
        if (
            simple_windows
            and detail["platform_commit"].get("recovery_material_removed") is not True
        ):
            detail["phase"] = "commit_cleanup_failed"
            operation.transition(
                "interrupted",
                message=(
                    "Target health passed; recovery-material cleanup will be retried"
                ),
                detail={**detail, "recovered": True},
            )
            self.store.save(operation)
            self._journal(
                operation,
                detail,
                phase="commit_cleanup_failed",
                status="interrupted",
            )
            return False
        detail["phase"] = "committed"
        detail["progress"] = 100
        operation.transition(
            "succeeded",
            message=(
                f"Upgrade to {package.version} committed after restart"
                if package is not None
                else (
                    "Upgrade commit finalized after restart without the cached "
                    "target package"
                )
            ),
            detail={**detail, "recovered": True},
        )
        # Keep the journal recoverable until both the operation and terminal
        # journal writes succeed.  Retrying commit after either write fails is
        # safe even when the recovery directory was already removed.
        self.store.save(operation)
        self.store.retire_terminal_rollback_journals()
        self.archive_housekeeping.apply_retention()
        self._journal(
            operation,
            detail,
            phase="committed",
            status="committed",
        )
        self._retire_superseded_interrupted_upgrades()
        self.service.set_startup_maintenance_gate(False)
        return True

    async def _cleanup_platform_staging(
        self, detail: dict[str, Any]
    ) -> str | None:
        staged = dict(detail.get("platform_staged") or {})
        if not staged:
            cleanup_transaction = getattr(
                self.platform_adapter,
                "cleanup_transaction",
                None,
            )
            if callable(cleanup_transaction):
                try:
                    await cleanup_transaction(str(detail.get("transaction_id") or ""))
                except Exception as exc:
                    return str(exc) or type(exc).__name__
            return None
        cleanup = getattr(self.platform_adapter, "cleanup", None)
        if not callable(cleanup):
            return None
        try:
            await cleanup(staged)
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
            verified_name = metadata.get("verified_path")
            if type(verified_name) is not str:
                raise UpgradeCompatibilityError(
                    "Verified package path metadata is missing"
                )
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
                or artifact != available_artifact
            ):
                raise UpgradeCompatibilityError(
                    "Downloaded package no longer matches verified release metadata"
                )
            bundle_path: Path | None = None
            bundle_manifest: dict[str, Any] | None = None
            if artifact.get("platform") == "windows":
                if (
                    artifact.get("purpose") != "velopack-bundle"
                    or artifact.get("filename") != VELOPACK_BUNDLE_NAME
                ):
                    raise UpgradeCompatibilityError(
                        "Windows automatic updates require the Velopack bundle"
                    )
                bundle_manifest = metadata.get("bundle_manifest")
                payload_name = metadata.get("payload_verified_path")
                generation = metadata.get("generation")
                if (
                    not isinstance(bundle_manifest, dict)
                ):
                    raise UpgradeCompatibilityError(
                        "Verified Velopack bundle payload metadata is missing"
                    )
                verified_name, payload_name = (
                    _validate_windows_generation_names(
                        generation,
                        verified_name,
                        payload_name,
                    )
                )
                packages_root = assert_contained_no_reparse(
                    self.layout.manager_packages_dir,
                    root=self.layout.root,
                    allow_missing=False,
                )
                assert_directory_no_reparse(packages_root)
                trusted_version_dir = assert_contained_no_reparse(
                    version_dir,
                    root=packages_root,
                    allow_missing=False,
                )
                assert_directory_no_reparse(trusted_version_dir)
                bundle_path = trusted_version_dir / verified_name
                package_path = trusted_version_dir / payload_name
                assert_contained_no_reparse(
                    bundle_path,
                    root=trusted_version_dir,
                    allow_missing=False,
                )
                assert_contained_no_reparse(
                    package_path,
                    root=trusted_version_dir,
                    allow_missing=False,
                )
                try:
                    validated = validate_velopack_bundle(
                        bundle_path,
                        expected_dicepp_version=target_version,
                        expected_channel=available.get("channel"),
                        expected_size=artifact["size"],
                        expected_sha256=artifact["sha256"],
                    )
                except VelopackBundleError as exc:
                    raise UpgradeCompatibilityError(
                        f"Downloaded Velopack bundle is invalid: {exc}"
                    ) from exc
                with open_regular_binary_no_follow(package_path) as payload:
                    payload_info = os.fstat(payload.fileno())
                    payload_digest = _sha256_handle(payload)
                if (
                    validated.manifest != bundle_manifest
                    or payload_info.st_nlink != 1
                    or payload_info.st_size != validated.nupkg_size
                    or payload_digest != validated.nupkg_sha256
                ):
                    raise UpgradeCompatibilityError(
                        "Downloaded Velopack payload no longer matches its bundle"
                    )
            else:
                if verified_name != filename:
                    raise UpgradeCompatibilityError(
                        "Downloaded package path differs from verified metadata"
                    )
                path = version_dir / verified_name
                if (
                    path.is_symlink()
                    or not path.is_file()
                    or path.stat().st_size != artifact["size"]
                    or _sha256_file(path) != artifact["sha256"]
                ):
                    raise UpgradeCompatibilityError(
                        "Downloaded package no longer matches verified release metadata"
                    )
                package_path = path
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
            path=package_path,
            metadata_path=metadata_path,
            artifact=artifact,
            release={**available, "fallbacks": metadata.get("fallbacks", {})},
            bundle_path=bundle_path,
            bundle_manifest=bundle_manifest,
        )

    def _record_running(
        self,
        operation: ManagerOperation,
        detail: dict[str, Any],
        running: list[str],
    ) -> None:
        detail["original_running"] = list(running)
        detail["runtime_state_captured"] = True
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

    def _fault(self, phase: str) -> None:
        if self.fault_hook is not None:
            self.fault_hook(phase)


def _validate_linux_manifest(
    manifest: dict[str, Any], package: VerifiedUpgradePackage
) -> None:
    required = {
        "format_version",
        "linux_manager_handoff_protocol",
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
    if (
        manifest["linux_manager_handoff_protocol"]
        != LINUX_MANAGER_HANDOFF_FORMAT
    ):
        raise UpgradeCompatibilityError(
            "Linux package Manager handoff protocol is unsupported",
            code="linux_handoff_protocol_unsupported",
        )
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
    if "manager" in change_scope and manifest.get(
        "linux_manager_handoff_protocol"
    ) != LINUX_MANAGER_HANDOFF_FORMAT:
        raise UpgradeCompatibilityError(
            "A Release that changes Manager without a supported handoff "
            "protocol requires manual deployment",
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


def _sha256_file(path: Path) -> str:
    with path.open("rb") as handle:
        return _sha256_handle(handle)


def _sha256_handle(handle) -> str:
    handle.seek(0)
    digest = hashlib.sha256()
    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
        digest.update(chunk)
    return digest.hexdigest()


def _validate_windows_generation_names(
    generation: Any,
    bundle_name: Any,
    payload_name: Any,
) -> tuple[str, str]:
    """Validate untrusted metadata without constructing or touching a path."""

    if (
        type(generation) is not str
        or type(bundle_name) is not str
        or type(payload_name) is not str
        or not _VELOPACK_GENERATION_ID_RE.fullmatch(generation)
        or bundle_name != f"velopack-{generation}.win-x64.zip"
        or payload_name != f"payload-{generation}.nupkg"
    ):
        raise UpgradeCompatibilityError(
            "Verified Velopack generation paths are unsafe or inconsistent"
        )
    return bundle_name, payload_name


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
