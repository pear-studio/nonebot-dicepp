from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import aiosqlite
import pytest
import pytest_asyncio

from core.communication import (
    MessageMetaData,
    MessageRecallEvent,
    MessageSender,
    PostSendEvent,
)
from core.data import LogRepository
from core.data.schema import ensure_bot_log_schema
from module.common.log import LogRuntime

pytestmark = [pytest.mark.integration, pytest.mark.log]

NOW = datetime(2026, 7, 20, 17, 0, 0)


class _FakeBot:
    def __init__(self) -> None:
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
    bot = _FakeBot()
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
