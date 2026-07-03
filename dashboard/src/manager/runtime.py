"""Runtime backend contract and Dashboard-local unavailable implementation."""

from __future__ import annotations

from typing import Protocol

from .models import BotRuntimeStatus, ManagerAction, RuntimeLogs


class RuntimeOperationUnsupported(Exception):
    """Raised when the Dashboard-local placeholder backend cannot operate bots."""


class RuntimeBackend(Protocol):
    """Contract shared by future DockerRuntime and ProcessRuntime backends."""

    async def status(self, bot_ids: list[str]) -> dict[str, BotRuntimeStatus]:
        """Return runtime state for the requested bots."""

    async def operate(
        self,
        bot_id: str,
        action: ManagerAction,
        request_detail: dict | None = None,
    ) -> BotRuntimeStatus:
        """Apply a lifecycle operation and return the resulting runtime state."""

    async def logs(self, bot_id: str, lines: int) -> RuntimeLogs:
        """Return diagnostic logs for a bot when supported by the backend."""

    async def runtime_logs(self, lines: int) -> RuntimeLogs:
        """Return global runtime logs when supported by the backend."""


class UnavailableRuntimeBackend:
    """Default runtime backend before Docker/Process runtimes are connected."""

    async def status(self, bot_ids: list[str]) -> dict[str, BotRuntimeStatus]:
        return {
            bot_id: BotRuntimeStatus(
                bot_id=bot_id,
                runtime_state="unknown",
                health="unavailable",
                message="Current runtime backend not connected / not implemented",
            )
            for bot_id in bot_ids
        }

    async def operate(
        self,
        bot_id: str,
        action: ManagerAction,
        request_detail: dict | None = None,
    ) -> BotRuntimeStatus:
        raise RuntimeOperationUnsupported(
            "Current runtime backend not connected / not implemented"
        )

    async def logs(self, bot_id: str, lines: int) -> RuntimeLogs:
        raise RuntimeOperationUnsupported(
            "Current runtime backend does not support logs"
        )

    async def runtime_logs(self, lines: int) -> RuntimeLogs:
        raise RuntimeOperationUnsupported(
            "Current runtime backend does not support runtime logs"
        )
