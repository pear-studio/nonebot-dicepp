"""Composition root for the standalone Manager process."""

from __future__ import annotations

import os
import shlex
from pathlib import Path

from dicepp_control.control_token import ensure_token

from .archive_coordinator import ArchiveCoordinator
from .config import ManagerSettings
from .control import ControlChannelService
from .discovery import RuntimeUnitDiscovery
from .docker_runtime import DockerRuntimeAdapter, DockerSocketRuntimeAdapter
from .docker_upgrade import DockerSocketUpgradeExecutor
from .owner import ManagerOwnerLock
from .process_runtime import ProcessRuntimeAdapter
from .release import ReleaseManager
from .runtime import RuntimeAdapter, UnavailableRuntimeAdapter
from .service import ManagerService
from .store import ManagerOperationStore
from .upgrade import (
    LinuxBundleUpgradeAdapter,
    UnsupportedUpgradeAdapter,
    UpgradeCoordinator,
    SimpleWindowsVelopackUpgradeAdapter,
)


def create_runtime_adapter(settings: ManagerSettings) -> RuntimeAdapter:
    if settings.runtime in {"", "unavailable"}:
        return UnavailableRuntimeAdapter()
    if settings.runtime == "process":
        return ProcessRuntimeAdapter(
            runtime_unit_id=settings.runtime_unit_id,
            command=settings.process_command,
            cwd=settings.process_cwd,
            stop_timeout=settings.process_stop_timeout,
            log_path=settings.layout.runtime_log,
            identity_path=settings.layout.manager_state_dir / "runtime-process.json",
        )
    if settings.runtime == "docker":
        if settings.docker_command.startswith("unix://"):
            return DockerSocketRuntimeAdapter(
                socket_path=settings.docker_command.removeprefix("unix://"),
                allowed_runtime_units={settings.runtime_unit_id},
                timeout=settings.docker_timeout,
            )
        return DockerRuntimeAdapter(
            docker_command=settings.docker_command,
            allowed_runtime_units={settings.runtime_unit_id},
            timeout=settings.docker_timeout,
        )
    raise ValueError(f"Unsupported Manager runtime adapter: {settings.runtime!r}")


def create_manager_service(settings: ManagerSettings) -> ManagerService:
    for directory in (
        settings.layout.manager_state_dir,
        settings.layout.manager_control_dir,
        settings.layout.manager_packages_dir,
        settings.layout.manager_backups_dir,
        settings.layout.manager_recovery_dir,
    ):
        directory.mkdir(parents=True, exist_ok=True)
    owner = ManagerOwnerLock(settings.layout.manager_state_dir)
    owner.acquire()
    try:
        adapter = create_runtime_adapter(settings)
        discovery = RuntimeUnitDiscovery(
            settings.layout,
            runtime_unit_id=settings.runtime_unit_id,
            adapter=settings.runtime,
        )
        store = ManagerOperationStore(settings.layout.manager_db)
        store.recover_incomplete_operations()
        service = ManagerService(
            unit_provider=discovery.list_units,
            runtime_adapter=adapter,
            store=store,
            state_dir=settings.layout.manager_state_dir,
            owner_lock=owner,
        )
        ensure_token(settings.layout.root)
        service.control_service = ControlChannelService(
            project_root=settings.layout.root,
            known_bot_ids=lambda: _configured_bot_ids(settings.layout),
            heartbeat_timeout=settings.control_heartbeat_timeout,
            reload_timeout=settings.control_reload_timeout,
        )
        service.archive_coordinator = ArchiveCoordinator(
            layout=settings.layout,
            service=service,
            control_probe=service.control_service.probe,
        )
        service.release_manager = ReleaseManager(
            layout=settings.layout,
            github_api=settings.github_api,
            protected_versions_loader=store.protected_upgrade_versions,
        )
        platform_adapter = _create_upgrade_adapter(
            settings,
            adapter,
        )
        service.upgrade_coordinator = UpgradeCoordinator(
            layout=settings.layout,
            service=service,
            archive_coordinator=service.archive_coordinator,
            release_manager=service.release_manager,
            platform_adapter=platform_adapter,
        )
        return service
    except BaseException:
        owner.release()
        raise


def _create_upgrade_adapter(
    settings: ManagerSettings,
    runtime_adapter: RuntimeAdapter,
):
    if os.name == "nt":
        configured = os.environ.get("DICEPP_VELOPACK_APPLY_COMMAND", "")
        update_exe = settings.layout.root / "Update.exe"
        launcher = settings.layout.root / "DicePP.exe"
        if configured:
            install_command = shlex.split(configured, posix=False)
        elif update_exe.is_file():
            install_command = [
                str(update_exe),
                "--rootDir",
                str(settings.layout.root),
                "--packageDir",
                "{package_dir}",
                "apply",
                "--waitPid",
                "{wait_pid}",
                "-p",
                "{package}",
                "--",
                "--background",
            ]
        else:
            install_command = []
        if install_command and launcher.is_file():
            return SimpleWindowsVelopackUpgradeAdapter(
                layout=settings.layout,
                install_command=install_command,
                launcher_path=launcher,
            )
        return UnsupportedUpgradeAdapter(
            "windows",
            "Velopack command boundary is not configured; use a manual Windows update",
        )

    compose = settings.layout.root / "docker-compose.yml"
    if isinstance(runtime_adapter, DockerSocketRuntimeAdapter) and compose.is_file():
        from .docker_handoff import DockerHandoffExecutor

        return LinuxBundleUpgradeAdapter(
            layout=settings.layout,
            executor=DockerSocketUpgradeExecutor(runtime_adapter),
            handoff_executor=DockerHandoffExecutor(runtime_adapter),
            current_compose=compose,
        )
    return UnsupportedUpgradeAdapter(
        "linux",
        "Automatic Linux installation requires the Docker socket adapter and "
        "a read-only current docker-compose.yml mount",
    )


def _configured_bot_ids(layout) -> set[str]:
    directory = layout.config_bots_dir
    if not directory.is_dir():
        return set()
    return {
        path.stem
        for path in directory.glob("*.json")
        if path.is_file() and path.stem != "_template"
    }


__all__ = ["create_manager_service", "create_runtime_adapter"]
