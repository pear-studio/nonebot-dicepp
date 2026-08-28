"""
create_persona 全路径冒烟测试

验证 `create_persona()` 成功路径上所有组件可正确组装：
- CharacterLoader, PersonaDataStore, DeepSeekTextModelClient, MessagePort, ChatOrchestrator, LifeSimulator
- SessionManager, LLMCallCoordinator, DecayCalculator

使用真实 PersonaConfig（Pydantic 模型）替代 MagicMock，
以确保所有 from_persona() 配置映射路径都覆盖到——这才是 rc6 缺字段崩溃的入口。

外部依赖策略：
- CharacterLoader → monkey-patch FakeLoader（无需真实角色卡 YAML）
- bot.db._db     → 内存 aiosqlite（schema 语句真实运行）
- DeepSeek 客户端构造 → 真实（不发起网络请求）
"""

import aiosqlite
import pytest
from unittest.mock import MagicMock, AsyncMock

from plugins.DicePP.module.persona.factory import create_persona, PersonaApp, _make_resolve_query_db
from plugins.DicePP.core.config.pydantic_models import (
    PersonaConfig,
    UserConfig,
)


# ============================================================
# Fake CharacterLoader
# ============================================================


class FakeCharacterLoader:
    """返回最小有效 Character mock，不含真实文件 I/O。"""

    def __init__(self, character_path: str):
        self.character_path = character_path

    def load(self, character_name: str):
        char = MagicMock()
        char.name = character_name
        char.character_id = character_name
        char.description = "Smoke test character"
        char.personality = "Friendly and helpful"
        char.scenario = ""
        char.mes_example = ""
        char.system_prompt = ""
        char.character_book = None

        ext = MagicMock()
        ext.relation_labels = []
        ext.world = ""
        ext.daily_events_count = 5
        ext.event_day_start_hour = 8
        ext.event_day_end_hour = 22
        ext.event_jitter_minutes = 60
        ext.event_day_start_jitter_minutes = 30
        ext.event_day_end_jitter_minutes = 30
        ext.refuse_messages = None
        ext.share_message_examples = None
        ext.sleep_messages = None
        ext.image_gen_style = ""
        ext.image_gen_appearance = ""
        char.extensions = ext

        char.get_relation_labels.return_value = [
            "好感不足", "初见", "友人", "亲密", "恋人",
        ]
        return char


# ============================================================
# Helper factories
# ============================================================


def _make_persona_config() -> PersonaConfig:
    """最小可成功初始化 Persona 模块的 PersonaConfig。

    关闭所有可选子系统以减小依赖范围，只保留必需配置。
    """
    return PersonaConfig(
        enabled=True,
        character_path="/tmp/smoke_chars",
        character_name="test_char",
        # 关闭不需要的子系统
        trace_enabled=False,
        quota_check_enabled=False,
        whitelist_enabled=False,
        group_activity_enabled=False,
        decay_enabled=False,
        proactive_enabled=False,
        character_life_enabled=False,
        relationship_refuse_enabled=False,
        daily_limit=9999,
        segment_max_chars=64,
        segment_hard_limit=128,
    )


async def _make_core_db():
    """创建内存 SQLite 连接模拟 ``bot.db._db``。"""
    conn = await aiosqlite.connect(":memory:")
    await conn.execute("PRAGMA foreign_keys=ON")
    return conn


# ============================================================
# Tests
# ============================================================


@pytest.mark.asyncio
async def test_persona_query_resolver_uses_group_config_for_group_and_private():
    """Persona 的群聊/私聊查询库都读取统一的 group_config 键。"""
    bot = MagicMock()
    bot.config.default_mode = "DND5E2024"
    rows = {
        "group-1": MagicMock(data={"query_database": "GROUP_DB"}),
        "__user__user-1": MagicMock(data={"query_database": "PRIVATE_DB"}),
    }

    async def get_config(key):
        return rows.get(key)

    bot.db.group_config.get = get_config

    resolve_db = _make_resolve_query_db(bot)

    assert await resolve_db("user-1", "group-1") == "GROUP_DB"
    assert await resolve_db("user-1", "") == "PRIVATE_DB"


class TestCreatePersonaSuccess:
    """create_persona 成功路径冒烟测试。"""

    @pytest.mark.asyncio
    async def test_create_persona_returns_persona_app(
        self, monkeypatch,
    ):
        """走完整 create_persona 路径，验证返回 PersonaApp 实例。"""
        # ── 1. Patch CharacterLoader ────────────────────────
        monkeypatch.setattr(
            'plugins.DicePP.module.persona.factory.CharacterLoader',
            FakeCharacterLoader,
        )

        # ── 3. 构造 Bot mock ──────────────────────────────
        bot = MagicMock()
        bot.account = "test_bot_smoke"
        bot.user_config = UserConfig(deepseek_api_key="sk-test")
        bot.config.persona_ai = _make_persona_config()
        bot.config.master = ""          # 避免发送启动报告
        bot.config.timezone = "Asia/Shanghai"

        # ── 4. 真实数据库连接 ────────────────────────────
        core_db = await _make_core_db()
        bot.db = MagicMock()
        bot.db._db = core_db
        bot.db.query = MagicMock()
        bot.db.group_config = MagicMock()
        bot.db.group_config.get = AsyncMock(
            return_value=MagicMock(data={"query_database": "DND5E2024"}),
        )
        bot.db.user_stat = MagicMock()
        bot.db.user_stat.get = AsyncMock(return_value=None)

        # ── 5. 代理（供 MessagePort 使用） ────────────────
        bot.proxy = MagicMock()
        bot.proxy.process_bot_command = AsyncMock()

        # ── 6. 执行 ──────────────────────────────────────
        app = await create_persona(bot)

        # ── 7. 断言 ──────────────────────────────────────
        assert app is not None, "create_persona 应返回 PersonaApp 实例"
        assert isinstance(app, PersonaApp)
        assert app.chat is not None, "chat 句柄不应为空"
        assert app.life is not None, "life 句柄不应为空"
        assert app.store is not None, "store 句柄不应为空"
        assert app.port is not None, "port 句柄不应为空"
        assert app.current_character_name == "test_char"
        system_prompt = app.chat._context_builder.build_static_prompt()
        assert "【回复长度】" in system_prompt
        assert "单段上限 64 字，总字数硬上限 128 字" in system_prompt

        # ── 8. 清理 ──────────────────────────────────────
        await app.store.close()
        await core_db.close()

    @pytest.mark.asyncio
    async def test_persona_app_handles_and_methods(
        self, monkeypatch,
    ):
        """验证 PersonaApp 的四个句柄类型及公有方法可安全调用。"""
        monkeypatch.setattr(
            'plugins.DicePP.module.persona.factory.CharacterLoader',
            FakeCharacterLoader,
        )

        bot = MagicMock()
        bot.account = "test_bot_smoke2"
        bot.user_config = UserConfig(deepseek_api_key="sk-test")
        bot.config.persona_ai = _make_persona_config()
        bot.config.master = ""
        bot.config.timezone = "Asia/Shanghai"

        core_db = await _make_core_db()
        bot.db = MagicMock()
        bot.db._db = core_db
        bot.db.query = MagicMock()
        bot.db.group_config = MagicMock()
        bot.db.group_config.get = AsyncMock(
            return_value=MagicMock(data={"query_database": "DND5E2024"}),
        )
        bot.db.user_stat = MagicMock()
        bot.db.user_stat.get = AsyncMock(return_value=None)
        bot.proxy = MagicMock()
        bot.proxy.process_bot_command = AsyncMock()

        app = await create_persona(bot)

        # 验证所有四个句柄就位
        assert app.chat is not None
        assert app.life is not None
        assert app.store is not None
        assert app.port is not None

        # 验证公有辅助方法不抛异常
        client = app.get_client()
        assert client is not None

        scheduler = app.get_scheduler()
        if scheduler is not None:
            status = scheduler.get_status()
            assert isinstance(status, dict)

        decay_calc = app.get_decay_calculator()
        assert decay_calc is not None

        rel_labels = app.get_relation_labels()
        assert isinstance(rel_labels, list)

        # 清理
        await app.store.close()
        await core_db.close()


class TestCreatePersonaFromPersonaMappings:
    """验证所有 from_persona() 配置映射方法在真实 PersonaConfig 上工作。

    这些测试是 rc6 类型崩溃的专项防护：如果 PersonaConfig 缺失某个字段，
    from_persona 会抛出 AttributeError，而 MagicMock 会静默掩盖。
    """

    def test_decay_config_from_persona(self):
        """DecayConfig.from_persona 映射所有字段。"""
        from plugins.DicePP.module.persona.game.decay import DecayConfig
        config = _make_persona_config()
        dc = DecayConfig.from_persona(config)
        assert dc.enabled == config.decay_enabled

    def test_chat_config_from_persona(self):
        """ChatConfig.from_persona 映射所有字段。"""
        from plugins.DicePP.module.persona.chat.chat_config import ChatConfig
        config = _make_persona_config()
        cc = ChatConfig.from_persona(config)
        assert cc.max_history_turns == config.max_history_turns
        assert cc.timezone == config.timezone

    def test_character_life_config_from_persona(self):
        """CharacterLifeConfig.from_persona 映射所有字段。"""
        from plugins.DicePP.module.persona.life.character_life import (
            CharacterLifeConfig,
        )
        config = _make_persona_config()
        clc = CharacterLifeConfig.from_persona(config)
        assert clc.enabled == config.character_life_enabled

    def test_proactive_config_from_persona(self):
        """ProactiveConfig.from_persona 映射所有字段。"""
        from plugins.DicePP.module.persona.life.proactive_config import (
            ProactiveConfig,
        )
        config = _make_persona_config()
        pc = ProactiveConfig.from_persona(config)
        assert pc.enabled == config.proactive_enabled

    def test_life_config_from_persona(self):
        """LifeConfig.from_persona 映射所有字段。"""
        from plugins.DicePP.module.persona.life.simulator import LifeConfig
        config = _make_persona_config()
        lc = LifeConfig.from_persona(config)
        assert lc.timezone == config.timezone

    def test_chat_config_default_session_budget(self):
        """ChatConfig 默认 session token budget 应与常量一致。"""
        from plugins.DicePP.module.persona.chat.chat_config import ChatConfig
        from plugins.DicePP.module.persona.data.models import (
            DEFAULT_SESSION_TOKEN_BUDGET,
        )
        config = _make_persona_config()
        cc = ChatConfig.from_persona(config)
        assert cc.private_session_token_budget == DEFAULT_SESSION_TOKEN_BUDGET
        assert cc.group_session_token_budget == DEFAULT_SESSION_TOKEN_BUDGET

    def test_decay_config_defaults(self):
        """DecayConfig.from_persona 在禁用 decay 时使用正确的默认值。"""
        from plugins.DicePP.module.persona.game.decay import DecayConfig
        config = _make_persona_config()
        dc = DecayConfig.from_persona(config)
        assert dc.familiarity_half_life_days == 35
        assert dc.intimacy_half_life_days == 21
