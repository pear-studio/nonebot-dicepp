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
async def test_shutdown_with_dispatcher_awaits_both():
    """shutdown 在有 segment_dispatcher 时同时 await dispatcher 和 store"""
    bot = MagicMock()
    bot.config.persona_ai = MagicMock()
    bot.config.persona_ai.enabled = True

    app = _make_app()
    app.segment_dispatcher = AsyncMock()
    app.store = AsyncMock()

    cmd = PersonaCommand(bot)
    cmd.app = app

    await cmd.shutdown()

    app.segment_dispatcher.shutdown.assert_awaited_once()
    app.store.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_shutdown_without_dispatcher_only_closes_store():
    """shutdown 在 segment_dispatcher=None 时仅执行 store.close()"""
    bot = MagicMock()
    bot.config.persona_ai = MagicMock()
    bot.config.persona_ai.enabled = True

    app = _make_app()
    app.segment_dispatcher = None
    app.store = AsyncMock()

    cmd = PersonaCommand(bot)
    cmd.app = app

    await cmd.shutdown()

    app.store.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_persona_app_shutdown_delegates_to_session_manager():
    """PersonaApp.shutdown 委托给 session_manager.shutdown"""
    app = _make_app()
    app.session_manager = AsyncMock()

    await app.shutdown()

    app.session_manager.shutdown.assert_awaited_once()


@pytest.mark.asyncio
async def test_persona_app_shutdown_no_session_manager():
    """PersonaApp.shutdown 在 session_manager=None 时安全跳过"""
    app = _make_app()
    app.session_manager = None

    # Should not raise
    await app.shutdown()
