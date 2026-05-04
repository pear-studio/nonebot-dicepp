"""PersonaApp 顶层 API 单元测试

PersonaApp 是工厂 create_persona 的返回值，持有 chat/life/store/port 四个句柄。
测试聚焦 `update_character` 的正确传播。
"""

import pytest
from unittest.mock import MagicMock

from plugins.DicePP.module.persona.factory import PersonaApp


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
