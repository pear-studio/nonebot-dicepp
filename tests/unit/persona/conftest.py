"""共享测试工具 — mock provider / router 工厂函数、temp_db fixture"""
import asyncio
from typing import Optional, List, Dict, Any

import pytest
from unittest.mock import MagicMock, AsyncMock

from plugins.DicePP.module.persona.data.models import UserLLMConfig


class MockDataStore:
    """配额/白名单测试通用 mock store。"""

    def __init__(self):
        self._usage: dict = {}
        self._whitelist_users: set = set()
        self._whitelist_groups: set = set()
        self._user_configs: dict = {}

    async def get_daily_usage(self, user_id: str, date: str) -> int:
        return self._usage.get((user_id, date), 0)

    async def increment_daily_usage(self, user_id: str, date: str) -> None:
        self._usage[(user_id, date)] = self._usage.get((user_id, date), 0) + 1

    async def is_user_whitelisted(self, user_id: str) -> bool:
        return user_id in self._whitelist_users

    async def is_group_whitelisted(self, group_id: str) -> bool:
        return group_id in self._whitelist_groups

    async def get_user_llm_config(self, user_id: str):
        return self._user_configs.get(user_id)

    async def insert_agent_run(self, **kwargs):
        return "run_id"

    async def update_run(self, run_id: str, **kwargs):
        pass

    async def insert_agent_event(self, **kwargs):
        pass

    def add_whitelist_user(self, user_id: str):
        self._whitelist_users.add(user_id)

    def add_whitelist_group(self, group_id: str):
        self._whitelist_groups.add(group_id)

    def set_user_config(self, user_id: str, config: UserLLMConfig):
        self._user_configs[user_id] = config


class MockQuotaConfig:
    """配额/白名单测试通用 mock config。"""

    def __init__(self):
        self.whitelist_enabled = True
        self.timezone = "Asia/Shanghai"
        self.quota_exceeded_message = "今日配额已用完（{limit}次）"


def make_mock_provider():
    """创建单个 mock LLM provider，generate 为 AsyncMock。"""
    provider = MagicMock()
    provider.generate = AsyncMock()
    return provider


def make_mock_providers():
    """创建 mock providers dict（用于 LLMRouter 构造）。"""
    provider = MagicMock()
    provider.api_key = "fake"
    provider.base_url = "http://localhost"
    provider.max_concurrent = None
    model = MagicMock()
    model.name = "fake"
    model.category = "llm"
    model.capabilities = ["text", "tool_calls"]
    model.quality = 0.9
    model.cost = 0.5
    model.circuit_breaker = None
    provider.models = [model]
    return {"fake": provider}


def _make_tool_registry():
    """创建含 4 个 life 工具的 ToolRegistry，供测试共用。"""
    from plugins.DicePP.module.persona.tools.registry import ToolRegistry, ToolDomain
    from plugins.DicePP.module.persona.tools.collecting import (
        RECORD_EVENT_TOOL,
        RECORD_REACTION_TOOL,
        RECORD_DIARY_ENTRY_TOOL,
        RECORD_SHARE_MESSAGE_TOOL,
        life_collecting_executor,
    )
    registry = ToolRegistry()
    registry.register(ToolDomain.LIFE, RECORD_EVENT_TOOL, life_collecting_executor)
    registry.register(ToolDomain.LIFE, RECORD_REACTION_TOOL, life_collecting_executor)
    registry.register(ToolDomain.LIFE, RECORD_DIARY_ENTRY_TOOL, life_collecting_executor)
    registry.register(ToolDomain.LIFE, RECORD_SHARE_MESSAGE_TOOL, life_collecting_executor)
    return registry


def make_mock_runtime(monkeypatch):
    """为 AgentRuntime.run 挂载 mock，通过 router 属性动态控制行为。

    测试设置 router._pending_tool_args (dict) 来模拟工具收集路径；
    设置 router._pending_final_output (str) 来控制回退路径的 final_text。

    供 test_scoring.py / test_event_agent.py / test_generate_share_message.py 使用。
    """
    from plugins.DicePP.module.persona.agent.runtime import AgentRuntime
    from plugins.DicePP.module.persona.agent.loop import AgentRunResult

    async def fake_run(self, messages, user_id, group_id, tool_registry, **kwargs):
        router = self._router
        pending_args = getattr(router, '_pending_tool_args', None)
        if pending_args is not None and tool_registry is not None:
            specs = tool_registry.list_tools()
            if specs:
                await specs[0].executor(**pending_args)
        final_output = getattr(router, '_pending_final_output', 'ok')
        return AgentRunResult(
            run_id="test",
            turn_id="test",
            status="completed",
            final_reason="direct_content",
            final_text=final_output,
            delivery_performed=True,
        )

    monkeypatch.setattr(AgentRuntime, "run", fake_run)


@pytest.fixture
async def temp_db():
    import aiosqlite
    from plugins.DicePP.module.persona.data.store import PersonaDataStore

    async with aiosqlite.connect(":memory:") as persona_db, \
         aiosqlite.connect(":memory:") as core_db:
        await persona_db.execute("PRAGMA foreign_keys=ON")
        store = PersonaDataStore(":memory:", core_db)
        store._persona_db = persona_db
        await store.ensure_tables()
        yield store


# ── L2 对话链测试工具 ───────────────────────────────────────────────────────


def _make_chat_tool_registry():
    """创建含 send_reply_segment + generate_image 的旧 ToolRegistry。

    ChatSession._chat_with_tools() 内部调用 build_registry() 将此 registry
    转换为新 ToolRegistry，EXTERNAL_ACTION 工具由 AgentLoop 路由到 sink。
    """
    from plugins.DicePP.module.persona.tools.registry import ToolRegistry, ToolDomain, ToolDef

    async def _unused_executor(args: dict, ctx) -> str:
        """占位 executor — EXTERNAL_ACTION 工具在 tool_bridge.py:build_registry()
        中会被替换为运行时生成的新 executor，此函数永不被调用。"""
        return "ok"

    registry = ToolRegistry()
    registry.register(
        ToolDomain.CHAT,
        ToolDef(
            name="send_reply_segment",
            description="发送回复分段。phase=interim 表示中间段，phase=final 表示最后一段。",
            parameters={
                "type": "object",
                "properties": {
                    "content": {"type": "string", "description": "该段回复文本"},
                    "image_url": {"type": "string", "description": "图片URL"},
                    "delay_before": {
                        "type": "number",
                        "description": "发送前等待秒数",
                    },
                    "phase": {
                        "type": "string",
                        "enum": ["interim", "final"],
                        "description": "分段阶段",
                    },
                },
                "required": ["content", "phase"],
            },
        ),
        _unused_executor,  # EXTERNAL_ACTION 工具：executor 由 tool_bridge.py:build_registry() 在运行时生成，此值不被使用
    )
    registry.register(
        ToolDomain.CHAT,
        ToolDef(
            name="generate_image",
            description="生成图片。根据文本描述生成图片。",
            parameters={
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "图片描述"},
                },
                "required": ["prompt"],
            },
        ),
        _unused_executor,  # EXTERNAL_ACTION 工具：executor 由 tool_bridge.py:build_registry() 在运行时生成，此值不被使用
    )
    return registry


def build_conversation_session(
    store,
    scripted_provider,
    *,
    fake_port=None,
    fake_image_gen=None,
    refuse_enabled: bool = False,
    refuse_messages: Optional[List[str]] = None,
    scoring_interval: int = 999,
):
    """构造完整依赖链的 ChatSession（L2 对话链测试用）。

    mock 边界设在 provider.generate()，之上所有组件均为真实实例。

    Args:
        store: PersonaDataStore（:memory: SQLite）
        scripted_provider: ScriptedProvider 实例
        fake_port: FakeMessagePort 实例（可选，用于捕获分段发送）
        fake_image_gen: FakeImageGenProvider 实例（可选，用于图片生成）
        refuse_enabled: 是否启用冷淡拒绝
        refuse_messages: 自定义拒绝文案
        scoring_interval: 评分间隔（默认 999 跳过评分）
    """
    from plugins.DicePP.module.persona.llm.router import LLMRouter
    from plugins.DicePP.core.config.pydantic_models import ProviderConfig, ModelConfig
    from plugins.DicePP.module.persona.character.models import Character
    from plugins.DicePP.module.persona.chat.chat_config import ChatConfig
    from plugins.DicePP.module.persona.chat.session_manager import SessionManager
    from plugins.DicePP.module.persona.chat.context import ContextBuilder
    from plugins.DicePP.module.persona.llm.coordinator import LLMCallCoordinator
    from plugins.DicePP.module.persona.chat.scoring_trigger import ScoringTrigger
    from plugins.DicePP.module.persona.chat.response_handler import ResponseHandler
    from plugins.DicePP.module.persona.chat.session import ChatSession

    # 1. LLMRouter — 构造空实例后手动注入 ScriptedProvider
    llm_model_config = ModelConfig(
        name="test-model",
        category="llm",
        capabilities=["text", "tool_calls"],
        quality=0.9,
        cost=0.5,
    )
    llm_provider_config = ProviderConfig(
        api_key="fake-key",
        base_url="http://localhost",
        models=[llm_model_config],
    )

    router = LLMRouter(
        providers={},
        global_max_concurrent=10,
        timeout=60,
        daily_limit=999,
        quota_check_enabled=False,
        data_store=None,
        trace_enabled=False,
    )

    llm_key = ("test", "test-model")
    router._model_providers[llm_key] = scripted_provider
    router._model_configs[llm_key] = llm_model_config
    router._llm_models.append(llm_key)
    router._semaphores["test"] = asyncio.Semaphore(10)
    router.stats["test"] = {"requests": 0, "errors": 0}
    router._providers["test"] = llm_provider_config

    # 2. FakeImageGenProvider 注入 gen 槽位
    if fake_image_gen is not None:
        gen_model_config = ModelConfig(
            name="test-gen-model",
            category="gen",
            capabilities=["image"],
            quality=0.9,
            cost=0.5,
        )
        gen_key = ("test-gen", "test-gen-model")
        router._model_providers[gen_key] = fake_image_gen
        router._model_configs[gen_key] = gen_model_config
        router._gen_models.append(gen_key)
        router._semaphores["test-gen"] = asyncio.Semaphore(10)
        router.stats["test-gen"] = {"requests": 0, "errors": 0}
        gen_provider_config = ProviderConfig(
            api_key="fake-key",
            base_url="http://localhost",
            models=[gen_model_config],
        )
        router._providers["test-gen"] = gen_provider_config

    # 3. Character
    character = Character(name="TestBot")
    if refuse_messages is not None:
        character.extensions.refuse_messages = refuse_messages

    # 4. ChatConfig
    config = ChatConfig(
        relationship_refuse_enabled=refuse_enabled,
        scoring_interval=scoring_interval,
        timezone="Asia/Shanghai",
    )

    # 5. SessionManager
    session_manager = SessionManager(store=store, config=config)

    # 6. ContextBuilder
    context_builder = ContextBuilder(character)

    # 7. LLMCallCoordinator
    coordinator = LLMCallCoordinator()

    # 8. ScoringTrigger（scoring_agent=None → 跳过 LLM 评分）
    scoring_trigger = ScoringTrigger(
        store=store,
        scoring_agent=None,
        decay_calculator=None,
        character=character,
        config=config,
    )

    # 9. ResponseHandler
    response_handler = ResponseHandler(store=store, port=fake_port)

    # 10. ToolRegistry（旧）
    tool_registry = _make_chat_tool_registry()

    # 11. ChatSession — 组装
    session = ChatSession(
        store=store,
        router=router,
        tool_registry=tool_registry,
        coordinator=coordinator,
        character=character,
        config=config,
        scoring_trigger=scoring_trigger,
        response_handler=response_handler,
        context_builder=context_builder,
        session_manager=session_manager,
    )

    return session
