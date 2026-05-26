"""PersonaApp 顶层 API 单元测试

PersonaApp 是工厂 create_persona 的返回值，持有 chat/life/store/port 四个句柄。
测试聚焦 `update_character` 的正确传播。
"""

import pytest
from unittest.mock import MagicMock, AsyncMock

from plugins.DicePP.module.persona.factory import PersonaApp
from plugins.DicePP.module.persona.command import PersonaCommand


def _make_app() -> PersonaApp:
    """构造 PersonaApp 的最小骨架（只关心 character 字段）"""
    chat = MagicMock()
    chat.character = MagicMock(name="OldChar")
    chat.context_builder = MagicMock()
    chat.context_builder.character = chat.character

    character_life = MagicMock()
    character_life.character = chat.character

    scheduler = MagicMock()
    scheduler.character = chat.character

    life = MagicMock()
    life.character = chat.character
    life.character_life = character_life
    life.scheduler = scheduler

    store = MagicMock()
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
async def test_update_character_handles_missing_scheduler():
    """scheduler 缺失时不应抛异常（保持向前兼容）"""
    app = _make_app()
    app.life.scheduler = None
    new_char = MagicMock(name="NewChar")

    # 不应抛异常
    await app.update_character(new_char)

    app.chat.update_character.assert_called_once_with(new_char)
    app.life.update_character.assert_called_once_with(new_char)


@pytest.mark.asyncio
async def test_chat_with_user_returns_empty_str_when_delivery_performed():
    """分段模式下 ChatSession.chat 返回空字符串（delivery 已由 runtime 完成），PersonaApp 应透传"""
    app = _make_app()
    app.chat.chat = AsyncMock(return_value="")

    result = await app.chat_with_user("u1", "g1", "hello", "nick")

    assert result == ""
    app.chat.chat.assert_awaited_once_with("u1", "g1", "hello", "nick")


@pytest.mark.asyncio
async def test_chat_with_user_returns_str_when_not_segmented():
    """非分段模式下 ChatSession.chat 返回字符串，PersonaApp 应透传字符串"""
    app = _make_app()
    app.chat.chat = AsyncMock(return_value="hello world")

    result = await app.chat_with_user("u1", "g1", "hello", "nick")

    assert result == "hello world"
    app.chat.chat.assert_awaited_once_with("u1", "g1", "hello", "nick")


@pytest.mark.asyncio
async def test_shutdown_calls_segment_dispatcher_shutdown():
    """PersonaCommand.shutdown 应调用 app.segment_dispatcher.shutdown()"""
    bot = MagicMock()
    bot.config.persona_ai = MagicMock()
    bot.config.persona_ai.enabled = True

    app = _make_app()
    app.segment_dispatcher = AsyncMock()

    cmd = PersonaCommand(bot)
    cmd.app = app

    await cmd.shutdown()

    app.segment_dispatcher.shutdown.assert_awaited_once()


@pytest.mark.asyncio
async def test_shutdown_handles_none_dispatcher():
    """PersonaCommand.shutdown 在 segment_dispatcher 为 None 时不应抛异常"""
    bot = MagicMock()
    bot.config.persona_ai = MagicMock()
    bot.config.persona_ai.enabled = True

    app = _make_app()
    app.segment_dispatcher = None

    cmd = PersonaCommand(bot)
    cmd.app = app

    # 不应抛异常
    await cmd.shutdown()
