"""Manager core state machine."""

from __future__ import annotations

import copy
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
from .models import ManagerAction, ManagerOperation, RuntimeUnit
from .runtime import RuntimeAdapter, RuntimeOperationUnsupported
from .store import ManagerOperationStore


class UnknownRuntimeUnit(LookupError):
    pass


class OperationConflict(RuntimeError):
    def __init__(self, operation: ManagerOperation) -> None:
        self.operation = operation
        super().__init__(operation.message)


class OperationFailed(RuntimeError):
    def __init__(self, operation: ManagerOperation, *, status_code: int = 500) -> None:
        self.operation = operation
        self.status_code = status_code
        super().__init__(operation.message)


@dataclass(frozen=True)
class MaintenanceSession:
    """Coordinator-safe adapter access while the instance lease is held."""

    _service: "ManagerService"

    async def operate_runtime_unit(
        self,
        runtime_unit_id: str,
        action: ManagerAction,
    ):
        self._service._require_unit(runtime_unit_id)
        return await self._service.runtime_adapter.operate(runtime_unit_id, action)


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
        unit_provider: Callable[[], list[RuntimeUnit]],
        runtime_adapter: RuntimeAdapter,
        store: ManagerOperationStore,
        state_dir,
        owner_lock: ManagerOwnerLock | None = None,
    ) -> None:
        self._unit_provider = unit_provider
        self.runtime_adapter = runtime_adapter
        self.store = store
        self.maintenance_lock = MaintenanceLock(state_dir)
        self._owner_lock = owner_lock
        self._running_by_unit: dict[str, ManagerOperation] = {}
        self._lifecycle_leases: dict[str, AbstractContextManager[None]] = {}
        self._maintenance_active = False
        self._startup_maintenance_active = False
        self._lock = threading.RLock()
        self.archive_coordinator = None
        # Created by the composition root.  Keeping it on the Manager service
        # makes the API, health gates and deployment factory share one owner.
        self.control_service = None
        self.release_manager = None
        self.upgrade_coordinator = None
        self._shutdown_callback: Callable[[str], None] | None = None
        self.shutdown_reason: str | None = None

    def close(self) -> None:
        with self._lock:
            leases = list(self._lifecycle_leases.values())
            self._lifecycle_leases.clear()
            self._running_by_unit.clear()
            for lease in leases:
                lease.__exit__(None, None, None)
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

    def units(self) -> list[RuntimeUnit]:
        return sorted(self._unit_provider(), key=lambda unit: unit.runtime_unit_id)

    async def status(self) -> dict:
        units = self.units()
        ids = [unit.runtime_unit_id for unit in units]
        statuses = await self.runtime_adapter.status(ids)
        rows = []
        bots = []
        for unit in units:
            status = statuses.get(unit.runtime_unit_id)
            runtime = status.to_dict() if status else {
                "runtime_unit_id": unit.runtime_unit_id,
                "runtime_state": "unknown",
                "health": "unknown",
                "message": "Runtime status unavailable",
                "detail": {},
            }
            with self._lock:
                operation = self._running_by_unit.get(unit.runtime_unit_id)
            manager = {
                "operation_status": operation.status if operation else "idle",
                "operation_id": operation.operation_id if operation else None,
                "action": operation.action if operation else None,
            }
            row = {**unit.to_dict(), "runtime": runtime, "manager": manager}
            rows.append(row)
            # API v1 compatibility is data-only; lifecycle routes remain unit based.
            for bot_id in unit.bot_ids:
                bots.append({
                    "bot_id": bot_id,
                    "runtime_unit_id": unit.runtime_unit_id,
                    "shared_process": unit.shared_process,
                    "runtime": runtime,
                    "manager": manager,
                })
        return {
            "runtime_units": rows,
            "bots": bots,
            "health": {
                "status": "ok",
                "runtime_adapter": type(self.runtime_adapter).__name__,
                "runtime_backend": type(self.runtime_adapter).__name__,
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

    def submit(self, runtime_unit_id: str, action: ManagerAction) -> ManagerOperation:
        self._require_unit(runtime_unit_id)
        with self._lock:
            running = self._running_by_unit.get(runtime_unit_id)
            if running is not None:
                rejected = ManagerOperation.create(runtime_unit_id, action)
                rejected.transition(
                    "rejected",
                    message=f"RuntimeUnit {runtime_unit_id} already has a running operation",
                    detail={"running_operation_id": running.operation_id},
                )
                self.store.save(rejected)
                raise OperationConflict(rejected)
            operation = ManagerOperation.create(runtime_unit_id, action)
            if self._maintenance_active or self._startup_maintenance_active:
                operation.transition(
                    "rejected",
                    message="Instance maintenance operation is active",
                    detail={"error": "maintenance_conflict"},
                )
                self.store.save(operation)
                raise OperationFailed(operation, status_code=409)
            lease = self.maintenance_lock.acquire(timeout=0)
            try:
                lease.__enter__()
            except MaintenanceConflict as exc:
                operation.transition(
                    "rejected",
                    message=str(exc),
                    detail={"error": "maintenance_conflict"},
                )
                self.store.save(operation)
                raise OperationFailed(operation, status_code=409) from exc
            try:
                self.store.save(operation)
            except BaseException:
                lease.__exit__(None, None, None)
                raise
            self._running_by_unit[runtime_unit_id] = operation
            self._lifecycle_leases[operation.operation_id] = lease
            return operation

    async def run(self, operation: ManagerOperation) -> ManagerOperation:
        try:
            operation.transition("running")
            self.store.save(operation)
            try:
                result = await self.runtime_adapter.operate(operation.runtime_unit_id, operation.action)
            except RuntimeOperationUnsupported as exc:
                operation.transition("failed", message=str(exc), detail={"error": "unsupported"})
                self.store.save(operation)
                raise OperationFailed(operation, status_code=501) from exc
            except Exception as exc:
                detail = getattr(exc, "detail", None)
                operation.transition(
                    "failed",
                    message=str(exc) or type(exc).__name__,
                    detail=copy.deepcopy(detail) if isinstance(detail, dict) else {},
                )
                self.store.save(operation)
                raise OperationFailed(operation) from exc
            else:
                operation.transition(
                    "succeeded",
                    message=f"RuntimeUnit {operation.action} succeeded",
                    detail={"runtime": result.to_dict()},
                )
                self.store.save(operation)
                return operation
        finally:
            with self._lock:
                if self._running_by_unit.get(operation.runtime_unit_id) is operation:
                    self._running_by_unit.pop(operation.runtime_unit_id, None)
                lease = self._lifecycle_leases.pop(operation.operation_id, None)
                if lease is not None:
                    lease.__exit__(None, None, None)

    async def operate(self, runtime_unit_id: str, action: ManagerAction) -> ManagerOperation:
        operation = self.submit(runtime_unit_id, action)
        return await self.run(operation)

    async def logs(self, runtime_unit_id: str, lines: int) -> dict:
        self._require_unit(runtime_unit_id)
        return (await self.runtime_adapter.logs(runtime_unit_id, lines)).to_dict()

    async def runtime_logs(self, lines: int) -> dict:
        return (await self.runtime_adapter.runtime_logs(lines)).to_dict()

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
            if self._running_by_unit:
                raise MaintenanceConflict("A runtime lifecycle operation is active")
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

    def _require_unit(self, runtime_unit_id: str) -> RuntimeUnit:
        for unit in self.units():
            if unit.runtime_unit_id == runtime_unit_id:
                return unit
        raise UnknownRuntimeUnit(f"RuntimeUnit not found: {runtime_unit_id}")
