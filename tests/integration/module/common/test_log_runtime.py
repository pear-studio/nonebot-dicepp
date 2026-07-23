from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import aiosqlite
import pytest
import pytest_asyncio

from plugins.DicePP.core.communication import (
    MessageMetaData,
    MessageRecallEvent,
    MessageSender,
    PostSendEvent,
)
from plugins.DicePP.core.bot import Bot
from plugins.DicePP.core.data import LogRepository
from plugins.DicePP.core.data.models import LogPublication
from plugins.DicePP.core.data.schema import ensure_bot_log_schema
from plugins.DicePP.module.common.log import LogRuntime
from plugins.DicePP.module.common.log.publisher import ProviderPublishResult


NOW = datetime(2026, 7, 20, 17, 0, 0)


class _FakeBot:
    def __init__(self, data_path: Path) -> None:
        self.account = "bot-1"
        self.data_path = str(data_path)
        self.proxy = None
        self.config = SimpleNamespace(
            log=SimpleNamespace(
                web=SimpleNamespace(
                    provider="dice_log_v105",
                    endpoint="",
                    token="",
                    timeout_seconds=15.0,
                )
            )
        )
        self.platform_hooks = []
        self.post_send_hooks = []
        self.recall_hooks = []
        self.unregister_order: list[str] = []

    def _add(self, collection: list, hook, label: str):
        collection.append(hook)

        def unregister() -> None:
            self.unregister_order.append(label)
            if hook in collection:
                collection.remove(hook)

        return unregister

    def add_platform_message_hook(self, hook):
        return self._add(self.platform_hooks, hook, "platform")

    def add_post_send_hook(self, hook):
        return self._add(self.post_send_hooks, hook, "post_send")

    def add_message_recall_hook(self, hook):
        return self._add(self.recall_hooks, hook, "recall")

    async def dispatch_platform(self, meta: MessageMetaData):
        return [await hook(meta) for hook in tuple(self.platform_hooks)]

    async def dispatch_post_send(self, event: PostSendEvent):
        return [await hook(event) for hook in tuple(self.post_send_hooks)]

    async def dispatch_recall(self, event: MessageRecallEvent):
        return [await hook(event) for hook in tuple(self.recall_hooks)]


@pytest_asyncio.fixture
async def runtime_parts(tmp_path: Path):
    path = tmp_path / "log.db"
    ensure_bot_log_schema(path)
    db = await aiosqlite.connect(path)
    await db.execute("PRAGMA foreign_keys=ON;")
    repository = LogRepository(db)
    bot = _FakeBot(tmp_path / "bot")
    runtime = LogRuntime(
        bot,
        repository,
        command_split="\n",
        clock=lambda: NOW,
    )
    try:
        yield bot, repository, runtime
    finally:
        runtime.close()
        await db.close()


def _user_meta(message_id: str, content: str = "玩家行动") -> MessageMetaData:
    sender = MessageSender("user-1", "玩家")
    sender.card = "调查员"
    meta = MessageMetaData(content, content, sender, group_id="g1")
    meta.message_id = message_id
    return meta


class _InjectedProvider:
    name = "injected"

    async def publish(self, projection, *, request_id, requested_by):
        return ProviderPublishResult("https://logs.test/injected")


@pytest.mark.asyncio
async def test_start_is_idempotent_and_exposes_one_service_and_recorder(
    runtime_parts,
):
    bot, _, runtime = runtime_parts
    service = runtime.service
    recorder = runtime.recorder

    runtime.start()
    runtime.start()

    assert runtime.started is True
    assert runtime.service is service
    assert runtime.recorder is recorder
    assert len(bot.platform_hooks) == 1
    assert len(bot.post_send_hooks) == 1
    assert len(bot.recall_hooks) == 1


@pytest.mark.asyncio
async def test_registered_hooks_route_all_events_to_real_recorder(runtime_parts):
    bot, repository, runtime = runtime_parts
    active = await runtime.service.turn_on("g1", "旅团", requested_by="owner")
    runtime.start()

    user_results = await bot.dispatch_platform(_user_meta("user-message"))
    bot_results = await bot.dispatch_post_send(
        PostSendEvent(
            group_id="g1",
            user_id="bot-1",
            role="assistant",
            message_type="command",
            content="检定成功",
            display_name="骰娘",
            platform_message_id="bot-message",
            history_stream_id=None,
        )
    )
    recalled_at = NOW + timedelta(minutes=1)
    recall_results = await bot.dispatch_recall(
        MessageRecallEvent("g1", "user-message", recalled_at)
    )

    assert user_results[0].recorded is True
    assert bot_results[0].recorded is True
    assert recall_results[0].marked_count == 1
    records = await repository.get_records(active.session.id)
    assert [(record.source, record.message_id) for record in records] == [
        ("user", "user-message"),
        ("bot", "bot-message"),
    ]
    assert records[0].recalled_at == recalled_at
    assert records[1].recalled_at is None


@pytest.mark.asyncio
async def test_close_is_idempotent_unregisters_in_reverse_and_stops_dispatch(
    runtime_parts,
):
    bot, repository, runtime = runtime_parts
    active = await runtime.service.turn_on("g1", "旅团", requested_by="owner")
    runtime.start()
    await bot.dispatch_platform(_user_meta("before-close"))

    runtime.close()
    runtime.close()
    after_results = await bot.dispatch_platform(_user_meta("after-close"))

    assert runtime.started is False
    assert bot.unregister_order == ["recall", "post_send", "platform"]
    assert bot.platform_hooks == []
    assert bot.post_send_hooks == []
    assert bot.recall_hooks == []
    assert after_results == []
    records = await repository.get_records(active.session.id)
    assert [record.message_id for record in records] == ["before-close"]


@pytest.mark.asyncio
async def test_latest_link_survives_unavailable_current_provider(runtime_parts):
    bot, repository, runtime = runtime_parts
    active = await runtime.service.turn_on("g1", "旅团", requested_by="owner")
    await repository.add_publication(
        LogPublication(
            request_id="historical-link",
            log_id=active.session.id,
            provider="retired_web",
            view="curated",
            created_at=NOW,
            published_at=NOW,
            url="https://logs.test/historical",
            status="success",
        )
    )
    bot.config.log.web.provider = "unsupported"
    runtime.refresh_publication_provider()

    publication = await runtime.latest_link(active.session.id)

    assert publication is not None
    assert publication.url == "https://logs.test/historical"


def test_refresh_publication_provider_uses_latest_web_config(runtime_parts):
    bot, _, runtime = runtime_parts
    initial_provider = runtime.publisher._provider
    assert initial_provider._endpoint == ""
    assert initial_provider._token == ""

    bot.config.log.web.endpoint = "https://logs.test/v105"
    bot.config.log.web.token = "new-token"
    bot.config.log.web.timeout_seconds = 9.0
    runtime.refresh_publication_provider()

    refreshed_provider = runtime.publisher._provider
    assert refreshed_provider is not initial_provider
    assert refreshed_provider._endpoint == "https://logs.test/v105"
    assert refreshed_provider._token == "new-token"
    assert refreshed_provider._timeout_seconds == 9.0

    bot.config.log.web.provider = "unknown"
    runtime.refresh_publication_provider()
    assert runtime.publisher is None
    assert runtime.publication_error == "不支持的 Web 日志服务：unknown"


def test_refresh_publication_provider_preserves_injected_provider(runtime_parts):
    bot, repository, _ = runtime_parts
    provider = _InjectedProvider()
    runtime = LogRuntime(bot, repository, publication_provider=provider)
    original_publisher = runtime.publisher

    bot.config.log.web.provider = "unknown"
    runtime.refresh_publication_provider()

    assert runtime.publisher is original_publisher
    assert runtime.publisher._provider is provider
    assert runtime.publication_error is None


def test_bot_reload_config_refreshes_log_publication_provider(monkeypatch):
    import plugins.DicePP.core.bot.dicebot as dicebot_module

    new_config = SimpleNamespace(
        log=SimpleNamespace(level="INFO"),
        persona="new-persona",
        persona_ai=SimpleNamespace(character_path="characters/new"),
        health_monitor=SimpleNamespace(
            heartbeat_timeout_seconds=11,
            consecutive_fail_threshold=4,
            failure_log_interval_seconds=23,
        ),
    )
    bot = object.__new__(Bot)
    bot._cfg_loader = SimpleNamespace(reload=lambda: new_config)
    bot.config = SimpleNamespace()
    bot._persona_loader = SimpleNamespace(
        reload=lambda: None,
        set_character_path=lambda _path: None,
    )
    bot.loc_helper = SimpleNamespace(
        reset_to_default=lambda: None,
        set_persona=lambda _persona: None,
    )
    bot.health_monitor = SimpleNamespace()
    refreshed_with = []
    bot.log_runtime = SimpleNamespace(
        refresh_publication_provider=lambda: refreshed_with.append(bot.config)
    )
    monkeypatch.setattr(dicebot_module, "configure_log_level", lambda _level: None)

    result = Bot.reload_config(bot)

    assert result is new_config
    assert refreshed_with == [new_config]
