"""Single-process coordination for Bot lifecycle and data maintenance."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from typing import TypeVar

from .bot_process import BotProcessController

T = TypeVar("T")


class BotNotStopped(RuntimeError):
    """A data-maintenance operation requires a stopped Bot."""


class BotRuntimeService:
    """Own the one process-local lock shared by lifecycle and data writes."""

    def __init__(
        self,
        controller: BotProcessController,
    ) -> None:
        self.controller = controller
        self._lock = threading.RLock()

    def operate_sync(self, action: str):
        if action not in {"start", "stop", "restart"}:
            raise ValueError("Bot action must be start, stop, or restart")
        with self._lock:
            return getattr(self.controller, action)()

    async def operate(self, action: str):
        return await asyncio.to_thread(self.operate_sync, action)

    def run_maintenance_sync(self, callback: Callable[[], T]) -> T:
        with self._lock:
            if self.controller.status().state != "stopped":
                raise BotNotStopped("Bot must be stopped before maintenance")
            return callback()

    async def run_maintenance(self, callback: Callable[[], T]) -> T:
        return await asyncio.to_thread(self.run_maintenance_sync, callback)
