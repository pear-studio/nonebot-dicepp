"""factory.create_persona 的异常契约测试

create_persona 在 Phase 2 之后用具名异常 PersonaInitError 报告初始化失败：

1. 角色卡缺失 → PersonaCharacterLoadError
2. providers 为空 → PersonaConfigError
3. 数据库未连接 → PersonaStorageError

模块禁用（config.enabled=False）仍返回 None，不抛异常——这是合法状态。

注意：_make_bot() 使用真实的 PersonaConfig() 而非 MagicMock()，
确保 from_persona 等配置映射路径不会因 MagicMock 的宽容性掩盖 AttributeError。
"""

import pytest
from unittest.mock import MagicMock, AsyncMock

from module.persona.factory import create_persona
from module.persona.exceptions import (
    PersonaInitError,
    PersonaConfigError,
    PersonaCharacterLoadError,
    PersonaStorageError,
)
from core.config.pydantic_models import PersonaConfig


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


def _make_providers_config():
    """最小有效的 providers 配置（真实 PersonaConfig 子字段）"""
    from core.config.pydantic_models import ProviderConfig, ModelConfig
    model = ModelConfig(
        name="gpt-4o",
        category="llm",
        capabilities=["text", "tool_calls"],
        quality=0.9,
        cost=0.5,
    )
    return {"openai": ProviderConfig(api_key="sk-test", base_url="https://api.openai.com/v1", models=[model])}


def _make_bot(
    *,
    enabled: bool = True,
    character_loaded=True,
    has_providers: bool = True,
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
        character_path="/tmp/chars",
        providers=_make_providers_config() if has_providers else {},
    )
    bot.config.persona_ai = cfg
    bot.config.persona = "test"

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
async def test_enabled_without_persona_returns_none(monkeypatch):
    """config.enabled=True 但 bot.config.persona=None 时返回 None"""
    bot = _make_bot(enabled=True)
    bot.config.persona = None
    result = await create_persona(bot)
    assert result is None


@pytest.mark.asyncio
async def test_enabled_with_empty_persona_returns_none(monkeypatch):
    """config.enabled=True 但 bot.config.persona='' 时返回 None"""
    bot = _make_bot(enabled=True)
    bot.config.persona = ""
    result = await create_persona(bot)
    assert result is None


@pytest.mark.asyncio
async def test_character_load_failure_raises(monkeypatch):
    """角色卡加载返回 None → 抛 PersonaCharacterLoadError"""
    bot = _make_bot()

    monkeypatch.setattr(
        "module.persona.factory.CharacterLoader",
        FakeLoaderReturnsNone,
    )

    with pytest.raises(PersonaCharacterLoadError) as excinfo:
        await create_persona(bot)
    assert "无法加载角色卡" in str(excinfo.value)
    assert isinstance(excinfo.value, PersonaInitError)


@pytest.mark.asyncio
async def test_missing_api_key_raises(monkeypatch):
    """providers 为空 → 抛 PersonaConfigError"""
    bot = _make_bot(has_providers=False)

    monkeypatch.setattr(
        "module.persona.factory.CharacterLoader",
        FakeLoaderReturnsChar,
    )

    with pytest.raises(PersonaConfigError) as excinfo:
        await create_persona(bot)
    assert "providers" in str(excinfo.value)
    assert isinstance(excinfo.value, PersonaInitError)


@pytest.mark.asyncio
async def test_db_not_connected_raises(monkeypatch):
    """bot.db._db 为 None → 抛 PersonaStorageError"""
    bot = _make_bot(db_connected=False)

    monkeypatch.setattr(
        "module.persona.factory.CharacterLoader",
        FakeLoaderReturnsChar,
    )

    # 防止真的构造 LLMRouter 失败：替换为 MagicMock
    monkeypatch.setattr(
        "module.persona.factory.LLMRouter",
        MagicMock(return_value=MagicMock()),
    )

    with pytest.raises(PersonaStorageError) as excinfo:
        await create_persona(bot)
    assert "数据库" in str(excinfo.value)
    assert isinstance(excinfo.value, PersonaInitError)
