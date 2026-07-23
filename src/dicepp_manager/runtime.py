"""Runtime adapter contract."""

from __future__ import annotations

from typing import Protocol

from .models import ManagerAction, RuntimeLogs, RuntimeUnitStatus


class RuntimeOperationUnsupported(RuntimeError):
    pass


class RuntimeAdapter(Protocol):
    async def status(self, runtime_unit_ids: list[str]) -> dict[str, RuntimeUnitStatus]: ...
    async def operate(self, runtime_unit_id: str, action: ManagerAction) -> RuntimeUnitStatus: ...
    async def logs(self, runtime_unit_id: str, lines: int) -> RuntimeLogs: ...
    async def runtime_logs(self, lines: int) -> RuntimeLogs: ...


class UnavailableRuntimeAdapter:
    async def status(self, runtime_unit_ids: list[str]) -> dict[str, RuntimeUnitStatus]:
        return {
            unit_id: RuntimeUnitStatus(
                unit_id,
                health="unavailable",
                message="Manager runtime adapter is unavailable",
            )
            for unit_id in runtime_unit_ids
        }

    async def operate(self, runtime_unit_id: str, action: ManagerAction) -> RuntimeUnitStatus:
        raise RuntimeOperationUnsupported("Manager runtime adapter is unavailable")

    async def logs(self, runtime_unit_id: str, lines: int) -> RuntimeLogs:
        raise RuntimeOperationUnsupported("Manager runtime logs are unavailable")

    async def runtime_logs(self, lines: int) -> RuntimeLogs:
        raise RuntimeOperationUnsupported("Manager runtime logs are unavailable")
