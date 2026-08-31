"""
create_persona 全路径冒烟测试

验证 `create_persona()` 成功路径上所有组件可正确组装：
- CharacterLoader, PersonaDataStore, DeepSeekTextModelClient, MessagePort, ChatOrchestrator, LifeSimulator
- SessionManager, LLMCallCoordinator

使用真实 PersonaConfig（Pydantic 模型）替代 MagicMock，
以确保 CharacterLifeConfig 的 from_persona() 映射路径被真实配置覆盖到。

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
        ext.world = ""
        ext.daily_events_count = 5
        ext.event_day_start_hour = 8
        ext.event_day_end_hour = 22
        ext.event_jitter_minutes = 60
        ext.event_day_start_jitter_minutes = 30
        ext.event_day_end_jitter_minutes = 30
        ext.sleep_messages = None
        char.extensions = ext

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
        character_name="test_char",
        life_simulation_enabled=False,
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
        bot.user_config = UserConfig(deepseek_api_key="sk-test", daily_ai_limit=7)
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
        assert app.chat._daily_ai_limit == 7
        system_prompt = app.chat._context_builder.build_static_prompt()
        assert "【回复长度】" in system_prompt
        assert "单段上限 80 字，总字数硬上限 120 字" in system_prompt

        # ── 8. 清理 ──────────────────────────────────────
        await app.store.close()
        await core_db.close()

    @pytest.mark.asyncio
    async def test_create_persona_wires_suggest_action_into_chat_tools(
        self, monkeypatch,
    ):
        """生产装配后的 ChatAgent 工具集能异步评估并注入行动。"""
        import asyncio

        from plugins.DicePP.module.persona.agent.runtime_types import ToolExecutionContext
        from plugins.DicePP.module.persona.life.conversation import Conversation
        from plugins.DicePP.module.persona.life.conversation_scope import ConversationScope

        monkeypatch.setattr(
            'plugins.DicePP.module.persona.factory.CharacterLoader',
            FakeCharacterLoader,
        )

        bot = MagicMock()
        bot.account = "test_bot_suggest_action"
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
        assert app is not None
        assert app.chat._action_evaluator is not None
        assert app.chat._character_life is not None

        evaluated = []
        injected = []
        injected_event = asyncio.Event()

        class _Evaluator:
            async def evaluate(self, action_idea, ongoing_descriptions, *, user_id):
                evaluated.append((action_idea, ongoing_descriptions, user_id))
                return "approved", "符合当前状态"

        class _Life:
            def get_ongoing_activities(self):
                return []

            async def inject_spontaneous_event(self, action_idea):
                injected.append(action_idea)
                injected_event.set()
                return True

        app.chat._action_evaluator = _Evaluator()
        app.chat._character_life = _Life()

        agent = app.chat._ensure_agent(
            ConversationScope.for_private("u1"), Conversation(),
        )
        toolkit, _ = agent._build_chat_toolkit(
            delivery=None,
            interaction_id="i1",
            user_id="u1",
            group_id="",
            char_name="test_char",
        )
        tool = toolkit.tools["suggest_action"]
        result = await tool.handler(
            tool.args_schema(action_idea="去公园散步"),
            MagicMock(spec=ToolExecutionContext),
        )

        assert result.observation == "action noted"
        await asyncio.wait_for(injected_event.wait(), timeout=1)
        assert evaluated == [("去公园散步", [], "u1")]
        assert injected == ["去公园散步"]

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

        # 清理
        await app.store.close()
        await core_db.close()


class TestCreatePersonaFromPersonaMappings:
    """验证仍保留的 from_persona() 配置映射方法使用真实 PersonaConfig。

    这些测试是 rc6 类型崩溃的专项防护：如果 PersonaConfig 缺失某个字段，
    from_persona 会抛出 AttributeError，而 MagicMock 会静默掩盖。
    """

    def test_character_life_config_from_persona(self):
        """CharacterLifeConfig.from_persona 映射所有字段。"""
        from plugins.DicePP.module.persona.life.character_life import (
            CharacterLifeConfig,
        )
        config = _make_persona_config()
        clc = CharacterLifeConfig.from_persona(config)
        assert clc.enabled == config.life_simulation_enabled
