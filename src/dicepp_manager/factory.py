"""Composition root for the standalone Manager process."""

from __future__ import annotations

from .config import ManagerSettings
from .discovery import RuntimeUnitDiscovery
from .docker_runtime import DockerRuntimeAdapter, DockerSocketRuntimeAdapter
from .process_runtime import ProcessRuntimeAdapter
from .owner import ManagerOwnerLock
from .runtime import RuntimeAdapter, UnavailableRuntimeAdapter
from .service import ManagerService
from .store import ManagerOperationStore


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
        settings.layout.manager_packages_dir,
        settings.layout.manager_backups_dir,
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
        return ManagerService(
            unit_provider=discovery.list_units,
            runtime_adapter=adapter,
            store=store,
            state_dir=settings.layout.manager_state_dir,
            owner_lock=owner,
        )
    except BaseException:
        owner.release()
        raise
