"""Single-process coordination for Bot lifecycle and data maintenance."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from typing import TypeVar

from dicepp_data.instance_data import instance_data_marker_path

from .bot_process import BotProcessController

T = TypeVar("T")


class BotStartBlocked(RuntimeError):
    """The instance marker says that an import is still in progress."""


class BotNotStopped(RuntimeError):
    """A data-maintenance operation requires a stopped Bot."""


class BotRuntimeService:
    """Own the one process-local lock shared by lifecycle and data writes."""

    def __init__(
        self,
        controller: BotProcessController,
        *,
        layout,
    ) -> None:
        self.controller = controller
        self.layout = layout
        self._lock = threading.RLock()

    def _check_start_allowed(self) -> None:
        if instance_data_marker_path(self.layout).exists():
            raise BotStartBlocked(
                "Business data import is incomplete; clear the instance before starting Bot"
            )

    def operate_sync(self, action: str):
        if action not in {"start", "stop", "restart"}:
            raise ValueError("Bot action must be start, stop, or restart")
        with self._lock:
            if action in {"start", "restart"}:
                self._check_start_allowed()
            return getattr(self.controller, action)()

    async def operate(self, action: str):
        return await asyncio.to_thread(self.operate_sync, action)

    def run_maintenance_sync(self, callback: Callable[[], T]) -> T:
        with self._lock:
            return callback()

    async def run_maintenance(self, callback: Callable[[], T]) -> T:
        return await asyncio.to_thread(self.run_maintenance_sync, callback)
