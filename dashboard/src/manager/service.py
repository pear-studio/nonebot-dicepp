"""Manager Core for the Dashboard-embedded first batch."""

from __future__ import annotations

import copy
import threading
from collections.abc import Callable

from .backends import create_runtime_backend
from .models import (
    MANAGER_API_VERSION,
    OPERATION_SCHEMA_VERSION,
    ManagerAction,
    ManagerOperation,
    get_dicepp_version,
)
from .runtime import RuntimeBackend, RuntimeOperationUnsupported
from .store import ManagerOperationStore

_INTERNAL_RUNTIME_REQUEST_SOURCES = {
    "archive_restore",
}


class UnknownBot(Exception):
    def __init__(self, bot_id: str) -> None:
        self.bot_id = bot_id
        super().__init__(f"Bot not found: {bot_id}")


class OperationConflict(Exception):
    def __init__(self, operation: ManagerOperation) -> None:
        self.operation = operation
        super().__init__(operation.message)


class OperationFailed(Exception):
    def __init__(self, operation: ManagerOperation, *, status_code: int = 500) -> None:
        self.operation = operation
        self.status_code = status_code
        super().__init__(operation.message)


class ManagerService:
    """In-process Manager state machine and runtime facade."""

    def __init__(
        self,
        *,
        bot_status_provider: Callable[[], list[dict]],
        runtime_backend: RuntimeBackend | None = None,
        db_path: str | None = None,
        max_operations: int = 200,
    ) -> None:
        if max_operations <= 0:
            raise ValueError("max_operations must be greater than 0")
        self._bot_status_provider = bot_status_provider
        self.runtime_backend = runtime_backend or create_runtime_backend()
        self._operations: list[ManagerOperation] = []
        self._running_by_bot: dict[str, ManagerOperation] = {}
        self._lock = threading.RLock()
        self._max_operations = max_operations
        self._store = (
            ManagerOperationStore(db_path, max_operations=max_operations)
            if db_path
            else None
        )

    async def status(self) -> dict:
        bots = self._discovered_bots()
        bot_ids = [bot["bot_id"] for bot in bots]
        runtime_status = await self.runtime_backend.status(bot_ids)

        merged = []
        for bot in bots:
            bot_id = bot["bot_id"]
            runtime = runtime_status.get(bot_id)
            with self._lock:
                running = self._running_by_bot.get(bot_id)
            entry = dict(bot)
            entry["manager"] = {
                "operation_status": running.status if running else "idle",
                "operation_id": running.operation_id if running else None,
                "action": running.action if running else None,
            }
            entry["runtime"] = (
                runtime.to_dict()
                if runtime
                else {
                    "bot_id": bot_id,
                    "runtime_state": "unknown",
                    "health": "unknown",
                    "message": "Runtime status unavailable",
                    "detail": {},
                }
            )
            merged.append(entry)

        return {
            "bots": merged,
            "health": {
                "status": "ok",
                "runtime_backend": type(self.runtime_backend).__name__,
                "manager_api_version": MANAGER_API_VERSION,
                "operation_schema_version": OPERATION_SCHEMA_VERSION,
                "dicepp_version": get_dicepp_version(),
            },
        }

    def list_operations(self, limit: int = 50) -> list[dict]:
        if self._store is not None:
            return [op.to_dict() for op in self._store.list_recent(limit)]
        with self._lock:
            return [op.to_dict() for op in self._operations[:limit]]

    def replace_operation_detail(
        self,
        operation: ManagerOperation,
        detail: dict | None,
    ) -> None:
        with self._lock:
            operation.detail = self._copy_detail(detail) or {}
            self._save_operation(operation)

    async def logs(self, bot_id: str, lines: int) -> dict:
        if bot_id not in self._known_bot_ids():
            raise UnknownBot(bot_id)

        result = await self.runtime_backend.logs(bot_id, lines)
        return result.to_dict()

    async def runtime_logs(self, lines: int) -> dict:
        runtime_logs = getattr(self.runtime_backend, "runtime_logs", None)
        if runtime_logs is None:
            raise RuntimeOperationUnsupported(
                "Current runtime backend does not support runtime logs"
            )

        result = await runtime_logs(lines)
        return result.to_dict()

    async def operate(
        self,
        bot_id: str,
        action: ManagerAction,
        request_detail: dict | None = None,
    ) -> ManagerOperation:
        request_detail = self._copy_detail(request_detail)
        if bot_id not in self._known_bot_ids():
            raise UnknownBot(bot_id)

        with self._lock:
            running = self._running_by_bot.get(bot_id)
            if running is not None:
                rejected = self._new_operation(bot_id, action)
                self._mark_rejected(
                    rejected,
                    f"Bot {bot_id} already has a running operation",
                    self._with_request_detail(
                        {
                            "running_operation_id": running.operation_id,
                            "running_action": running.action,
                        },
                        action,
                        request_detail,
                    ),
                )
                raise OperationConflict(rejected)

            operation = self._new_operation(bot_id, action)
            self._mark_running(operation)
            self._running_by_bot[bot_id] = operation

        try:
            result = await self.runtime_backend.operate(
                bot_id,
                action,
                request_detail=self._copy_detail(request_detail),
        )
        except RuntimeOperationUnsupported as exc:
            message = self._runtime_failure_message(
                action,
                str(exc) or "Current runtime backend not connected / not implemented",
            )
            self._mark_failed(
                operation,
                message,
                self._with_request_detail(
                    {"error": "unsupported"},
                    action,
                    request_detail,
                ),
            )
            raise OperationFailed(operation, status_code=501) from exc
        except Exception as exc:
            message = self._runtime_failure_message(
                action,
                str(exc) or type(exc).__name__,
            )
            exc_detail = getattr(exc, "detail", None)
            failure_detail = dict(exc_detail) if isinstance(exc_detail, dict) else {}
            self._mark_failed(
                operation,
                message,
                self._with_request_detail(failure_detail, action, request_detail),
            )
            raise OperationFailed(operation) from exc
        else:
            self._mark_succeeded(
                operation,
                f"Manager operation {action} succeeded",
                self._with_request_detail(
                    {"runtime": result.to_dict()},
                    action,
                    request_detail,
                ),
            )
            return operation
        finally:
            with self._lock:
                if self._running_by_bot.get(bot_id) is operation:
                    self._running_by_bot.pop(bot_id, None)

    def _discovered_bots(self) -> list[dict]:
        return sorted(
            (dict(bot) for bot in self._bot_status_provider()),
            key=lambda item: item["bot_id"],
        )

    def _known_bot_ids(self) -> set[str]:
        return {bot["bot_id"] for bot in self._discovered_bots()}

    def _new_operation(self, bot_id: str, action: ManagerAction) -> ManagerOperation:
        with self._lock:
            operation = ManagerOperation.create(bot_id, action)
            self._operations.insert(0, operation)
            del self._operations[self._max_operations :]
            self._save_operation(operation)
            return operation

    def _mark_running(self, operation: ManagerOperation) -> None:
        with self._lock:
            operation.mark_running()
            self._save_operation(operation)

    def _mark_succeeded(
        self,
        operation: ManagerOperation,
        message: str = "",
        detail: dict | None = None,
    ) -> None:
        with self._lock:
            operation.mark_succeeded(message, detail)
            self._save_operation(operation)

    def _mark_failed(
        self,
        operation: ManagerOperation,
        message: str,
        detail: dict | None = None,
    ) -> None:
        with self._lock:
            operation.mark_failed(message, detail)
            self._save_operation(operation)

    def _mark_rejected(
        self,
        operation: ManagerOperation,
        message: str,
        detail: dict | None = None,
    ) -> None:
        with self._lock:
            operation.mark_rejected(message, detail)
            self._save_operation(operation)

    def _save_operation(self, operation: ManagerOperation) -> None:
        if self._store is not None:
            self._store.save(operation)

    def _with_request_detail(
        self,
        detail: dict,
        action: ManagerAction,
        request_detail: dict | None,
    ) -> dict:
        if self._is_internal_runtime_request(request_detail):
            return self._internal_runtime_operation_detail(detail, request_detail)
        if request_detail is None:
            return detail
        return {**detail, "request": self._copy_detail(request_detail)}

    def _runtime_failure_message(
        self,
        action: ManagerAction,
        message: str,
    ) -> str:
        return message

    def _is_internal_runtime_request(self, request_detail: dict | None) -> bool:
        if not isinstance(request_detail, dict):
            return False
        return request_detail.get("source") in _INTERNAL_RUNTIME_REQUEST_SOURCES

    def _internal_runtime_operation_detail(
        self,
        detail: dict,
        request_detail: dict | None,
    ) -> dict:
        safe_detail = {
            key: detail[key]
            for key in ("error", "running_operation_id", "running_action")
            if isinstance(detail.get(key), str)
        }
        if request_detail is not None:
            safe_detail["request"] = self._copy_detail(request_detail)
        return safe_detail

    def _copy_detail(self, detail: dict | None) -> dict | None:
        if detail is None:
            return None
        return copy.deepcopy(detail)
