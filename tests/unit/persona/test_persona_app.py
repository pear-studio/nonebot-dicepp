"""PersonaApp 顶层 API 单元测试

PersonaApp 是工厂 create_persona 的返回值，持有 chat/life/store/port 四个句柄。
测试聚焦 `update_character` 的正确传播。
"""

import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock

from plugins.DicePP.module.persona.factory import PersonaApp
from plugins.DicePP.module.persona.command import PersonaCommand
from plugins.DicePP.core.config.pydantic_models import PersonaConfig


def _make_app() -> PersonaApp:
    """构造 PersonaApp 的最小骨架（只关心 character 字段）"""
    chat = MagicMock()
    chat.character = MagicMock(name="OldChar")
    chat.context_builder = MagicMock()
    chat.context_builder.character = chat.character

    life = MagicMock()
    life.character = chat.character

    store = AsyncMock()
    port = MagicMock()

    return PersonaApp(chat=chat, life=life, store=store, port=port)


@pytest.mark.asyncio
async def test_update_character_propagates_to_all_subsystems():
    """update_character 应同步到所有持有 character 引用的子系统"""
    app = _make_app()
    new_char = MagicMock(name="NewChar")

    await app.update_character(new_char)

    app.chat.update_character.assert_called_once_with(new_char)
    app.life.update_character.assert_called_once_with(new_char)


@pytest.mark.asyncio
async def test_shutdown_closes_store():
    """shutdown 关闭 Persona store。"""
    bot = MagicMock()
    bot.config.persona_ai = PersonaConfig(enabled=True)

    app = _make_app()
    app.store = AsyncMock()

    cmd = PersonaCommand(bot)
    cmd.app = app

    await cmd.shutdown()

    app.store.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_shutdown_cancels_daily_then_sa_before_closing_store():
    """shutdown 按 daily → SA → store 的顺序收口，且禁止再调度日终任务。"""
    bot = MagicMock()
    bot.config.persona_ai = PersonaConfig(enabled=True)
    app = _make_app()
    order = []
    daily_started = asyncio.Event()
    sa_started = asyncio.Event()

    async def blocked(label, started):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            order.append(label)

    app.store.close = AsyncMock(side_effect=lambda: order.append("store"))
    cmd = PersonaCommand(bot)
    cmd.app = app
    cmd.enabled = True
    cmd._async_tick_daily_task = asyncio.create_task(
        blocked("daily", daily_started)
    )
    cmd._async_sa_daily_task = asyncio.create_task(blocked("sa", sa_started))
    await daily_started.wait()
    await sa_started.wait()

    await cmd.shutdown()

    assert order == ["daily", "sa", "store"]
    assert cmd._async_tick_daily_task is None
    assert cmd._async_sa_daily_task is None
    assert cmd.tick_daily() == []
    cmd._schedule_daily_planning("diary", "2026-08-07")
    assert cmd._async_sa_daily_task is None
