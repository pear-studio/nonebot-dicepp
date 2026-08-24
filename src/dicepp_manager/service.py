"""Manager core state machine."""

from __future__ import annotations

import threading
from collections.abc import Callable
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from typing import Iterator

from dicepp_meta import get_version

from .deployment import (
    DEPLOYMENT_SCHEMA_VERSION,
    MANAGER_API_VERSION,
    MANAGER_VERSION,
    MINIMUM_DASHBOARD_API_VERSION,
    OPERATION_SCHEMA_VERSION,
)
from .maintenance import MaintenanceConflict, MaintenanceLock
from .owner import ManagerOwnerLock
from .models import ManagerOperation
from .store import ManagerOperationStore


@dataclass(frozen=True)
class MaintenanceSession:
    """Exclusive archive/configuration maintenance lease.

    Bot lifecycle is owned by Dashboard's BotProcessController; Manager only
    serializes durable archive/configuration work.
    """

    _service: "ManagerService"


class MaintenanceReservation:
    """A non-reentrant maintenance lease that can cross an async task hand-off."""

    def __init__(
        self,
        service: "ManagerService",
        lease: AbstractContextManager[None],
    ) -> None:
        self._service = service
        self._lease = lease
        self._released = False
        self.session = MaintenanceSession(service)

    def __enter__(self) -> MaintenanceSession:
        return self.session

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.release()

    def release(self) -> None:
        with self._service._lock:
            if self._released:
                return
            self._lease.__exit__(None, None, None)
            self._released = True
            self._service._maintenance_active = False


class ManagerService:
    def __init__(
        self,
        *,
        store: ManagerOperationStore,
        state_dir,
        owner_lock: ManagerOwnerLock | None = None,
    ) -> None:
        self.store = store
        self.maintenance_lock = MaintenanceLock(state_dir)
        self._owner_lock = owner_lock
        self._maintenance_active = False
        self._startup_maintenance_active = False
        self._lock = threading.RLock()
        self.archive_coordinator = None
        # Coordinator-neutral boundaries shared by archive and runtime flows.
        # ArchiveCoordinator initializes them before the API root is composed.
        self.maintenance_runtime_support = None
        self.archive_housekeeping = None
        # Created by the composition root.  Keeping it on the Manager service
        # makes the API, health gates and deployment factory share one owner.
        self.control_service = None
        self._shutdown_callback: Callable[[str], None] | None = None
        self.shutdown_reason: str | None = None

    def close(self) -> None:
        with self._lock:
            self._maintenance_active = False
        if self._owner_lock is not None:
            self._owner_lock.release()
            self._owner_lock = None

    def set_shutdown_callback(self, callback: Callable[[str], None]) -> None:
        self._shutdown_callback = callback
        if self.shutdown_reason is not None:
            callback(self.shutdown_reason)

    def request_shutdown(self, reason: str) -> None:
        self.shutdown_reason = reason
        callback = self._shutdown_callback
        if callback is not None:
            callback(reason)

    async def status(self) -> dict:
        """Return Manager health without Bot lifecycle state."""
        return {
            "health": {
                "status": "ok",
                "manager_api_version": MANAGER_API_VERSION,
                "operation_schema_version": OPERATION_SCHEMA_VERSION,
                "deployment_schema_version": DEPLOYMENT_SCHEMA_VERSION,
                "manager_version": MANAGER_VERSION,
                "minimum_dashboard_api_version": MINIMUM_DASHBOARD_API_VERSION,
                "dicepp_version": get_version(),
            },
        }
    def list_operations(self, limit: int = 50) -> list[dict]:
        return [operation.to_dict() for operation in self.store.list_recent(limit)]

    def get_operation(self, operation_id: str) -> ManagerOperation | None:
        return self.store.get(operation_id)

    def reserve_maintenance(
        self,
        *,
        timeout: float = 0,
        allow_startup_recovery: bool = False,
    ) -> MaintenanceReservation:
        """Reserve instance maintenance before a durable operation is created.

        The reservation is intentionally acquired synchronously by the HTTP
        submission path and transferred to its critical background task.  This
        removes the gap where two requests could both create journals before
        either coordinator entered the maintenance context.
        """
        with self._lock:
            if self._maintenance_active:
                raise MaintenanceConflict("An instance maintenance operation is active")
            if self._startup_maintenance_active and not allow_startup_recovery:
                raise MaintenanceConflict("Startup maintenance recovery is active")
            lease = self.maintenance_lock.acquire(timeout=timeout)
            lease.__enter__()
            self._maintenance_active = True
            return MaintenanceReservation(self, lease)

    @contextmanager
    def maintenance(
        self,
        *,
        timeout: float = 0,
        allow_startup_recovery: bool = False,
    ) -> Iterator[MaintenanceSession]:
        reservation = self.reserve_maintenance(
            timeout=timeout,
            allow_startup_recovery=allow_startup_recovery,
        )
        try:
            yield reservation.session
        finally:
            reservation.release()

    def set_startup_maintenance_gate(self, active: bool) -> None:
        """Block lifecycle submissions while startup recovery awaits API bind."""
        with self._lock:
            self._startup_maintenance_active = active
