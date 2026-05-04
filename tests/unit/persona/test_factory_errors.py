"""factory.create_persona 的异常契约测试

create_persona 在 Phase 2 之后用具名异常 PersonaInitError 报告初始化失败：

1. 角色卡缺失 → PersonaCharacterLoadError
2. primary_api_key 缺失 → PersonaConfigError
3. 数据库未连接 → PersonaStorageError

模块禁用（config.enabled=False）仍返回 None，不抛异常——这是合法状态。
"""

import pytest
from unittest.mock import MagicMock, AsyncMock

from plugins.DicePP.module.persona.factory import create_persona
from plugins.DicePP.module.persona.exceptions import (
    PersonaInitError,
    PersonaConfigError,
    PersonaCharacterLoadError,
    PersonaStorageError,
)


def _make_bot(
    *,
    enabled: bool = True,
    character_loaded=True,
    primary_api_key: str = "sk-test",
    db_connected: bool = True,
) -> MagicMock:
    """构造最小可走 create_persona 前 3 步的 Bot mock"""
    bot = MagicMock()
    cfg = MagicMock()
    cfg.enabled = enabled
    cfg.character_path = "/tmp/chars"
    cfg.character_name = "test"
    cfg.primary_api_key = primary_api_key
    bot.config.persona_ai = cfg

    bot.db = MagicMock()
    bot.db._db = MagicMock() if db_connected else None
    return bot


@pytest.mark.asyncio
async def test_disabled_returns_none(monkeypatch):
    """config.enabled=False 时返回 None，不抛异常"""
    bot = _make_bot(enabled=False)
    result = await create_persona(bot)
    assert result is None


@pytest.mark.asyncio
async def test_character_load_failure_raises(monkeypatch):
    """角色卡加载返回 None → 抛 PersonaCharacterLoadError"""
    bot = _make_bot()

    class FakeLoader:
        def __init__(self, path):
            pass

        def load(self, name):
            return None

    monkeypatch.setattr(
        "plugins.DicePP.module.persona.factory.CharacterLoader",
        FakeLoader,
    )

    with pytest.raises(PersonaCharacterLoadError) as excinfo:
        await create_persona(bot)
    assert "无法加载角色卡" in str(excinfo.value)
    assert isinstance(excinfo.value, PersonaInitError)


@pytest.mark.asyncio
async def test_missing_api_key_raises(monkeypatch):
    """primary_api_key 为空 → 抛 PersonaConfigError"""
    bot = _make_bot(primary_api_key="")

    class FakeLoader:
        def __init__(self, path):
            pass

        def load(self, name):
            return MagicMock(name="char")  # 角色加载成功

    monkeypatch.setattr(
        "plugins.DicePP.module.persona.factory.CharacterLoader",
        FakeLoader,
    )

    with pytest.raises(PersonaConfigError) as excinfo:
        await create_persona(bot)
    assert "primary_api_key" in str(excinfo.value)
    assert isinstance(excinfo.value, PersonaInitError)


@pytest.mark.asyncio
async def test_db_not_connected_raises(monkeypatch):
    """bot.db._db 为 None → 抛 PersonaStorageError"""
    bot = _make_bot(db_connected=False)

    class FakeLoader:
        def __init__(self, path):
            pass

        def load(self, name):
            return MagicMock(name="char")

    monkeypatch.setattr(
        "plugins.DicePP.module.persona.factory.CharacterLoader",
        FakeLoader,
    )

    # 防止真的构造 LLMRouter 失败：替换为 MagicMock
    monkeypatch.setattr(
        "plugins.DicePP.module.persona.factory.LLMRouter",
        MagicMock(return_value=MagicMock()),
    )

    with pytest.raises(PersonaStorageError) as excinfo:
        await create_persona(bot)
    assert "数据库" in str(excinfo.value)
    assert isinstance(excinfo.value, PersonaInitError)
