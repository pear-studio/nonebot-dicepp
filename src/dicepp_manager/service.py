"""Manager core state machine."""

from __future__ import annotations

import threading
from collections.abc import Callable

from dicepp_meta import get_version

from .deployment import (
    DEPLOYMENT_SCHEMA_VERSION,
    MANAGER_API_VERSION,
    MANAGER_VERSION,
    MINIMUM_DASHBOARD_API_VERSION,
    OPERATION_SCHEMA_VERSION,
)
from .owner import ManagerOwnerLock
from .models import ManagerOperation
from .store import ManagerOperationStore


class ManagerService:
    def __init__(
        self,
        *,
        store: ManagerOperationStore,
        state_dir,
        owner_lock: ManagerOwnerLock | None = None,
    ) -> None:
        self.store = store
        self._owner_lock = owner_lock
        self._lock = threading.RLock()
        # Created by the composition root.  Keeping it on the Manager service
        # makes the API, health gates and deployment factory share one owner.
        self.control_service = None
        self._shutdown_callback: Callable[[str], None] | None = None
        self.shutdown_reason: str | None = None

    def close(self) -> None:
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
