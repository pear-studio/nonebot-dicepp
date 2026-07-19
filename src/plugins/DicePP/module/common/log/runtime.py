from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any, Protocol

from core.communication import MessageMetaData, MessageRecallEvent, PostSendEvent
from core.data.log_repository import LogRepository

from .recorder import LogRecorder
from .service import LogService


class LogHookHost(Protocol):
    def add_platform_message_hook(
        self, hook: Callable[[MessageMetaData], Any]
    ) -> Callable[[], None]: ...

    def add_post_send_hook(
        self, hook: Callable[[PostSendEvent], Any]
    ) -> Callable[[], None]: ...

    def add_message_recall_hook(
        self, hook: Callable[[MessageRecallEvent], Any]
    ) -> Callable[[], None]: ...


class LogRuntime:
    """Bot-scoped owner of Log's service, recorder, and hook registrations."""

    def __init__(
        self,
        bot: LogHookHost,
        repository: LogRepository,
        *,
        command_split: str = "\n",
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.service = LogService(repository, clock=clock)
        self.recorder = LogRecorder(
            repository,
            command_split=command_split,
            clock=clock,
        )
        self._bot = bot
        self._unregister_hooks: list[Callable[[], None]] = []

    @property
    def started(self) -> bool:
        return bool(self._unregister_hooks)

    def start(self) -> None:
        if self.started:
            return

        registered: list[Callable[[], None]] = []
        try:
            registered.append(
                self._bot.add_platform_message_hook(
                    self.recorder.record_user_message
                )
            )
            registered.append(
                self._bot.add_post_send_hook(self.recorder.record_bot_message)
            )
            registered.append(
                self._bot.add_message_recall_hook(self.recorder.mark_recalled)
            )
        except BaseException:
            for unregister in reversed(registered):
                unregister()
            raise
        self._unregister_hooks = registered

    def close(self) -> None:
        if not self._unregister_hooks:
            return
        unregister_hooks = self._unregister_hooks
        self._unregister_hooks = []
        for unregister in reversed(unregister_hooks):
            unregister()
