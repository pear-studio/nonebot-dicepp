from __future__ import annotations

from datetime import datetime
import os
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio

from plugins.DicePP.core.bot import Bot
from plugins.DicePP.core.config import Paths
from plugins.DicePP.core.communication import (
    MessageMetaData,
    MessageRecallEvent,
    MessageSender,
    PostSendEvent,
)
from plugins.DicePP.module.common.log import LogRecorder
from tests.support.fs_utils import rmtree_retry


def _repository_root() -> Path:
    return next(
        parent
        for parent in Path(__file__).resolve().parents
        if (parent / "pyproject.toml").is_file()
    )


@pytest_asyncio.fixture
async def uninitialized_log_bot():
    original_project_root = Paths.PROJECT_ROOT
    isolated_project_root = Path(os.environ["DICEPP_PROJECT_ROOT"])
    tracked_global = _repository_root() / "config" / "global.json"
    global_snapshot = tracked_global.read_bytes()
    Paths.configure_project_root(isolated_project_root)
    bot = None
    try:
        bot = Bot(
            f"test_log_runtime_{uuid4().hex[:12]}",
            no_tick=True,
        )
        bot.config.command_split = "\n"
        yield bot
    finally:
        try:
            if bot is not None:
                if bot.log_runtime is not None:
                    await bot.shutdown_async()
                else:
                    await bot.db.close()
                rmtree_retry(bot.data_path)
        finally:
            Paths.configure_project_root(original_project_root)
            if tracked_global.read_bytes() != global_snapshot:
                tracked_global.write_bytes(global_snapshot)


@pytest_asyncio.fixture
async def log_bot(uninitialized_log_bot: Bot):
    await uninitialized_log_bot.delay_init_command()
    return uninitialized_log_bot


@pytest.mark.asyncio
async def test_first_message_initializes_runtime_before_platform_dispatch(
    uninitialized_log_bot: Bot,
    monkeypatch,
):
    observed: list[MessageMetaData] = []
    original = LogRecorder.record_user_message

    async def observe_and_record(recorder, meta):
        observed.append(meta)
        return await original(recorder, meta)

    monkeypatch.setattr(LogRecorder, "record_user_message", observe_and_record)
    sender = MessageSender("user-first", "先遣玩家")
    meta = MessageMetaData("第一条消息", "第一条消息", sender, group_id="group-first")
    meta.message_id = "first-platform-message"

    assert uninitialized_log_bot._delay_init_done is False
    assert uninitialized_log_bot.log_runtime is None
    await uninitialized_log_bot.process_message(meta.plain_msg, meta)

    assert uninitialized_log_bot._delay_init_done is True
    assert uninitialized_log_bot.log_runtime is not None
    assert observed == [meta]


@pytest.mark.asyncio
async def test_real_bot_routes_platform_send_and_recall_once(log_bot: Bot):
    runtime = log_bot.log_runtime
    assert runtime is not None
    assert runtime.started is True
    active = await runtime.service.turn_on(
        "group-1",
        "集成测试",
        requested_by="owner-1",
    )

    sender = MessageSender("user-1", "玩家")
    sender.card = "调查员"
    meta = MessageMetaData(
        "第一段\n第二段",
        "第一段\n第二段",
        sender,
        group_id="group-1",
    )
    meta.message_id = "user-message-1"
    await log_bot.process_message(meta.plain_msg, meta)

    await log_bot.dispatch_post_send_event(
        PostSendEvent(
            group_id="group-1",
            user_id=log_bot.account,
            role="assistant",
            message_type="command",
            content="检定成功",
            display_name="骰娘",
            platform_message_id="bot-message-1",
            history_stream_id=None,
            history_managed_by_sender=True,
        )
    )
    recalled_at = datetime(2026, 7, 20, 18, 30, 0)
    await log_bot.dispatch_message_recall_event(
        MessageRecallEvent(
            group_id="group-1",
            platform_message_id="bot-message-1",
            recalled_at=recalled_at,
        )
    )

    records = await log_bot.db.log.get_records(active.session.id)
    assert [(record.source, record.message_id) for record in records] == [
        ("user", "user-message-1"),
        ("bot", "bot-message-1"),
    ]
    assert records[0].plain_content == "第一段\n第二段"
    assert records[0].recalled_at is None
    assert records[1].recalled_at == recalled_at

    await log_bot.delay_init_command()
    runtime.start()
    assert log_bot.log_runtime is runtime
    assert len(log_bot._platform_message_hooks) == 1
    assert len(log_bot._post_send_hooks) == 1
    assert len(log_bot._message_recall_hooks) == 1


@pytest.mark.asyncio
async def test_shutdown_unregisters_log_runtime_before_database_close(log_bot: Bot):
    original_close = log_bot.db.close
    close_observed = False

    async def assert_runtime_closed_then_close():
        nonlocal close_observed
        close_observed = True
        assert log_bot.log_runtime is None
        assert log_bot._platform_message_hooks == []
        assert log_bot._post_send_hooks == []
        assert log_bot._message_recall_hooks == []
        await original_close()

    log_bot.db.close = assert_runtime_closed_then_close
    await log_bot.shutdown_async()

    assert close_observed is True
    assert log_bot.log_runtime is None
