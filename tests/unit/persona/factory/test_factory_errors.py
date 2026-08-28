"""factory.create_persona 的异常契约测试

create_persona 在 Phase 2 之后用具名异常 PersonaInitError 报告初始化失败：

1. 角色卡缺失 → PersonaCharacterLoadError
2. DeepSeek API Key 为空 → PersonaConfigError
3. 数据库未连接 → PersonaStorageError

模块禁用（config.enabled=False）仍返回 None，不抛异常——这是合法状态。

注意：_make_bot() 使用真实的 PersonaConfig() 而非 MagicMock()，
确保 from_persona 等配置映射路径不会因 MagicMock 的宽容性掩盖 AttributeError。
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
from plugins.DicePP.core.config.pydantic_models import PersonaConfig, UserConfig


class FakeLoaderReturnsNone:
    """FakeLoader: load() 返回 None，模拟角色卡加载失败"""

    def __init__(self, path):
        pass

    def load(self, name):
        return None


class FakeLoaderReturnsChar:
    """FakeLoader: load() 返回 MagicMock，模拟角色卡加载成功"""

    def __init__(self, path):
        pass

    def load(self, name):
        return MagicMock(name="char")


def _make_bot(
    *,
    enabled: bool = True,
    character_loaded=True,
    has_api_key: bool = True,
    db_connected: bool = True,
) -> MagicMock:
    """构造最小可走 create_persona 前 3 步的 Bot mock

    config.persona_ai 使用真实 PersonaConfig() 替代 MagicMock，
    确保 CharacterLifeConfig.from_persona() 等配置映射路径不会
    因 MagicMock 的宽容性掩盖 AttributeError（rc6 生产 bug）。
    """
    bot = MagicMock()
    cfg = PersonaConfig(
        enabled=enabled,
        character_name="test",
    )
    bot.config.persona_ai = cfg
    bot.user_config = UserConfig(deepseek_api_key="sk-test" if has_api_key else "")

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

    monkeypatch.setattr(
        'plugins.DicePP.module.persona.factory.CharacterLoader',
        FakeLoaderReturnsNone,
    )

    with pytest.raises(PersonaCharacterLoadError) as excinfo:
        await create_persona(bot)
    assert "无法加载角色卡" in str(excinfo.value)
    assert isinstance(excinfo.value, PersonaInitError)


@pytest.mark.asyncio
async def test_character_loader_uses_persona_ai_character_name(monkeypatch):
    """角色加载始终使用 persona_ai.character_name。"""
    loaded_names = []

    class RecordingLoader:
        def __init__(self, path):
            pass

        def load(self, name):
            loaded_names.append(name)
            return None

    bot = _make_bot()
    bot.config.persona_ai.character_name = "configured-character"
    monkeypatch.setattr(
        "plugins.DicePP.module.persona.factory.CharacterLoader",
        RecordingLoader,
    )

    with pytest.raises(PersonaCharacterLoadError):
        await create_persona(bot)

    assert loaded_names == ["configured-character"]


@pytest.mark.asyncio
async def test_missing_api_key_raises(monkeypatch):
    """DeepSeek API Key 为空 → 抛 PersonaConfigError"""
    bot = _make_bot(has_api_key=False)

    monkeypatch.setattr(
        'plugins.DicePP.module.persona.factory.CharacterLoader',
        FakeLoaderReturnsChar,
    )

    with pytest.raises(PersonaConfigError) as excinfo:
        await create_persona(bot)
    assert "API Key" in str(excinfo.value)
    assert isinstance(excinfo.value, PersonaInitError)


@pytest.mark.asyncio
async def test_db_not_connected_raises(monkeypatch):
    """bot.db._db 为 None → 抛 PersonaStorageError"""
    bot = _make_bot(db_connected=False)

    monkeypatch.setattr(
        'plugins.DicePP.module.persona.factory.CharacterLoader',
        FakeLoaderReturnsChar,
    )

    with pytest.raises(PersonaStorageError) as excinfo:
        await create_persona(bot)
    assert "数据库" in str(excinfo.value)
    assert isinstance(excinfo.value, PersonaInitError)
