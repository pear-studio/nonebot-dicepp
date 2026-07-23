"""Composition root for the standalone Manager process."""

from __future__ import annotations

import os
import json
import urllib.error
import urllib.request
from datetime import datetime, timezone

from .archive_coordinator import ArchiveCoordinator
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
        service = ManagerService(
            unit_provider=discovery.list_units,
            runtime_adapter=adapter,
            store=store,
            state_dir=settings.layout.manager_state_dir,
            owner_lock=owner,
        )
        service.archive_coordinator = ArchiveCoordinator(
            layout=settings.layout,
            service=service,
            dashboard_probe=lambda: _dashboard_probe(),
            control_probe=lambda: _control_channel_probe(),
        )
        return service
    except BaseException:
        owner.release()
        raise


def _dashboard_health_payload() -> tuple[str, int | None, dict]:
    url = os.environ.get(
        "DICEPP_DASHBOARD_HEALTH_URL",
        "http://127.0.0.1:4090/api/health",
    )
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=3) as response:
            status = response.status
            raw = response.read(64 * 1024)
    except urllib.error.HTTPError:
        return url, None, {}
    except (OSError, urllib.error.URLError, TimeoutError):
        return url, None, {}
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        payload = {}
    return url, status, payload if isinstance(payload, dict) else {}


def _dashboard_probe() -> dict:
    url, status, payload = _dashboard_health_payload()
    valid = (
        status == 200
        and payload.get("status") == "ok"
        and payload.get("component") == "dashboard"
    )
    return {
        "ok": valid,
        "status": "ok" if valid else "failed",
        "http_status": status,
        "url": url,
    }


def _control_channel_probe() -> dict:
    url, status, payload = _dashboard_health_payload()
    if (
        status != 200
        or payload.get("status") != "ok"
        or payload.get("component") != "dashboard"
    ):
        return {
            "ok": False,
            "status": "failed",
            "url": url,
            "message": "Dashboard health is unavailable",
        }
    control = payload.get("control")
    raw_heartbeat = (
        control.get("latest_heartbeat") if isinstance(control, dict) else None
    )
    if not isinstance(raw_heartbeat, str) or not raw_heartbeat:
        return {
            "ok": False,
            "status": "failed",
            "url": url,
            "message": "No Bot control heartbeat",
        }
    try:
        heartbeat = datetime.fromisoformat(raw_heartbeat.replace("Z", "+00:00"))
        age = (
            datetime.now(timezone.utc) - heartbeat.astimezone(timezone.utc)
        ).total_seconds()
    except (TypeError, ValueError):
        return {
            "ok": False,
            "status": "failed",
            "url": url,
            "message": "Invalid Bot control heartbeat",
        }
    return {
        "ok": age <= 120,
        "status": "ok" if age <= 120 else "failed",
        "url": url,
        "heartbeat_age_seconds": age,
        "heartbeat": heartbeat.astimezone(timezone.utc).isoformat(),
    }
