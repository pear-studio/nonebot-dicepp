from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from plugins.DicePP.core.communication import MessageMetaData, MessageRecallEvent, PostSendEvent
from plugins.DicePP.core.data.log_repository import LogRepository
from plugins.DicePP.core.data.models import LogPublication

from .export_service import ExportBatchResult, LogExportCoordinator
from .providers import DiceLogV105Provider
from .publisher import (
    LogPublicationProvider,
    LogPublisher,
    PublicationResult,
)
from .recorder import LogRecorder
from .service import LogService
from .types import ExportRequest


class LogHookHost(Protocol):
    account: str
    data_path: str
    proxy: Any
    config: Any

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
        publication_provider: LogPublicationProvider | None = None,
    ) -> None:
        self._repository = repository
        self._clock = clock
        self._publication_provider_injected = publication_provider is not None
        self.service = LogService(repository, clock=clock)
        self.recorder = LogRecorder(
            repository,
            command_split=command_split,
            clock=clock,
        )
        bot_data_root = Path(bot.data_path)
        self.coordinator = LogExportCoordinator(
            repository,
            bot_data_root=bot_data_root,
            output_root=bot_data_root / "logs",
        )
        provider, provider_error = _resolve_publication_provider(
            bot,
            publication_provider,
        )
        self.publisher = (
            LogPublisher(repository, provider, clock=clock)
            if provider is not None
            else None
        )
        self.publication_error = provider_error
        self._bot = bot
        self._unregister_hooks: list[Callable[[], None]] = []

    def refresh_publication_provider(self) -> None:
        """Refresh config-backed Web publication state after a config reload."""
        if self._publication_provider_injected:
            return
        provider, provider_error = _resolve_publication_provider(self._bot, None)
        self.publisher = (
            LogPublisher(self._repository, provider, clock=self._clock)
            if provider is not None
            else None
        )
        self.publication_error = provider_error

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

    async def generate_and_deliver(
        self,
        request: ExportRequest,
    ) -> ExportBatchResult:
        batch = await self.coordinator.generate(request)
        proxy = self._bot.proxy
        if proxy is None:
            return batch
        return await self.coordinator.deliver(
            batch,
            proxy=proxy,
            account=self._bot.account,
            folder_name="跑团log",
        )

    async def publish(self, request: ExportRequest) -> PublicationResult:
        if self.publisher is None:
            raise LogPublicationUnavailableError(
                self.publication_error or "Web 日志发布未配置"
            )
        return await self.publisher.publish(request)

    async def latest_link(self, log_id: str) -> LogPublication | None:
        return await self._repository.get_latest_successful_publication(log_id)


class LogPublicationUnavailableError(RuntimeError):
    pass


def _resolve_publication_provider(
    bot: LogHookHost,
    injected: LogPublicationProvider | None,
) -> tuple[LogPublicationProvider | None, str | None]:
    if injected is not None:
        return injected, None
    web = bot.config.log.web
    provider_name = str(web.provider or "").strip()
    if provider_name != "dice_log_v105":
        return None, f"不支持的 Web 日志服务：{provider_name or '未配置'}"
    return (
        DiceLogV105Provider(
            str(web.endpoint or ""),
            token=str(web.token or ""),
            timeout_seconds=float(web.timeout_seconds),
        ),
        None,
    )
