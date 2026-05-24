"""
单元测试: Persona Event Agent（使用 ToolRegistry + run_via_loop）
"""
import json
import pytest
from datetime import datetime
from unittest.mock import MagicMock, AsyncMock

from plugins.DicePP.module.persona.life.event_agent import (
    EventGenerationAgent, EventContext, EventGenerationResult, EventReactionResult,
)
from plugins.DicePP.module.persona.llm.loop import LoopResult
from conftest import _make_tool_registry


def _make_router():
    router = MagicMock()
    router.data_store = None
    router.quota_check_enabled = False
    router.daily_limit = 20
    router.trace_enabled = False
    router.trace_max_age_days = 7
    router.config = None
    router._mock_tool_args = None
    router._mock_error = None

    async def _mock_run_via_loop(messages, tools, tool_registry=None, tool_domains=None,
                                   tool_ctx=None, selection=None, max_tool_rounds=5, **kwargs):
        if router._mock_error is not None:
            raise router._mock_error
        if tool_ctx is not None and tool_ctx.collected_args is not None and router._mock_tool_args is not None:
            tool_ctx.collected_args.append(router._mock_tool_args)
        return LoopResult(final_output="ok")

    router.run_via_loop = AsyncMock(side_effect=_mock_run_via_loop)
    return router


class TestGenerateDiary:
    @pytest.fixture
    def mock_router(self):
        return _make_router()

    @pytest.fixture
    def agent(self, mock_router):
        config = MagicMock()
        config.background_llm_timeout_seconds = 90
        config.background_llm_max_tool_rounds = 3
        return EventGenerationAgent(mock_router, _make_tool_registry(), config=config)

    @pytest.mark.asyncio
    async def test_generate_diary_success(self, agent, mock_router):
        mock_router._mock_tool_args = {"diary": "今天过得真充实，发生了很多有趣的事情。"}

        result = await agent.generate_diary(
            events=[{"description": "早上喝咖啡", "reaction": "感觉很清醒"}],
            character_name="测试角色", character_description="一个喜欢记录生活的人",
            yesterday_diary="昨天也很充实。")

        assert result == "今天过得真充实，发生了很多有趣的事情。"

    @pytest.mark.asyncio
    async def test_generate_diary_truncate_long(self, agent, mock_router):
        long_diary = "今天真是漫长的一天" * 60
        mock_router._mock_tool_args = {"diary": long_diary}

        result = await agent.generate_diary(
            events=[{"description": "事件", "reaction": "反应"}],
            character_name="角色", character_description="描述")

        assert len(result) <= 300
        assert result.endswith("...")

    @pytest.mark.asyncio
    async def test_generate_diary_fallback_on_exception(self, agent, mock_router):
        mock_router._mock_error = Exception("服务不可用")
        result = await agent.generate_diary(
            events=[{"description": "事件", "reaction": "反应"}],
            character_name="角色", character_description="描述")
        assert "太累了" in result

    @pytest.mark.asyncio
    async def test_generate_diary_no_collected(self, agent, mock_router):
        # _mock_tool_args 保持 None → run_via_loop 不写入 collected_args
        result = await agent.generate_diary(
            events=[{"description": "事件", "reaction": "反应"}],
            character_name="角色", character_description="描述")
        assert "太累了" in result

    @pytest.mark.asyncio
    async def test_generate_diary_without_yesterday(self, agent, mock_router):
        mock_router._mock_tool_args = {"diary": "今天的日记内容"}
        await agent.generate_diary(
            events=[{"description": "事件1", "reaction": "反应1"}],
            character_name="角色", character_description="描述", yesterday_diary=None)
        assert mock_router.run_via_loop.called

    @pytest.mark.asyncio
    async def test_generate_diary_with_yesterday(self, agent, mock_router):
        mock_router._mock_tool_args = {"diary": "今天的日记内容"}
        await agent.generate_diary(
            events=[{"description": "事件1", "reaction": "反应1"}],
            character_name="角色", character_description="描述",
            yesterday_diary="这是昨天的日记内容，写了很多字。")
        assert mock_router.run_via_loop.called

    @pytest.mark.asyncio
    async def test_generate_diary_empty_events(self, agent, mock_router):
        mock_router._mock_tool_args = {"diary": "今天没什么特别的事发生。"}
        result = await agent.generate_diary(events=[], character_name="角色", character_description="描述")
        assert result == "今天没什么特别的事发生。"

    @pytest.mark.asyncio
    async def test_generate_diary_includes_date_in_prompt(self, agent, mock_router, monkeypatch):
        """日记 prompt 必须包含真实日期，防止 LLM 错误推断月份（B-260522-8859d5）"""
        fake_now = datetime(2026, 5, 23, 14, 30, 0)
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.life.event_agent.persona_wall_now",
            lambda tz: fake_now,
        )
        mock_router._mock_tool_args = {"diary": "今天过得真充实"}

        await agent.generate_diary(
            events=[{"description": "事件", "reaction": "反应"}],
            character_name="角色", character_description="描述")

        user_msg = mock_router.run_via_loop.call_args.kwargs["messages"][1]["content"]
        assert "当前日期: 2026年05月23日" in user_msg


class TestGenerateEventResult:
    @pytest.fixture
    def mock_router(self):
        return _make_router()

    @pytest.fixture
    def agent(self, mock_router):
        config = MagicMock()
        config.background_llm_timeout_seconds = 90
        config.background_llm_max_tool_rounds = 3
        return EventGenerationAgent(mock_router, _make_tool_registry(), config=config)

    @pytest.mark.asyncio
    async def test_generate_event_result_success(self, agent, mock_router):
        mock_router._mock_tool_args = {"description": "窗外下雨了", "context_summary": "窗外下雨", "duration_minutes": 30}

        result = await agent.generate_event_result(EventContext(
            character_name="小雨", character_description="温柔的少女",
            world="", scenario="", recent_diaries=[], today_events=[],
            current_time=datetime(2024, 1, 1, 10, 0)))

        assert result.description == "窗外下雨了"
        assert result.context_summary == "窗外下雨"
        assert result.duration_minutes == 30
        assert "世界观设定专家" in result.system_prompt_digest

    @pytest.mark.asyncio
    async def test_generate_event_result_clamp_duration_max(self, agent, mock_router):
        mock_router._mock_tool_args = {"description": "事件", "context_summary": "摘要", "duration_minutes": 99999}

        result = await agent.generate_event_result(EventContext(
            character_name="小雨", character_description="温柔的少女",
            world="", scenario="", recent_diaries=[], today_events=[],
            current_time=datetime(2024, 1, 1, 10, 0)))

        assert result.duration_minutes == 2880

    @pytest.mark.asyncio
    async def test_generate_event_result_fallback(self, agent, mock_router):
        mock_router._mock_error = Exception("forced error")
        result = await agent.generate_event_result(EventContext(
            character_name="小雨", character_description="温柔的少女",
            world="", scenario="", recent_diaries=[], today_events=[],
            current_time=datetime(2024, 1, 1, 10, 0)))
        assert "休息" in result.description

    @pytest.mark.asyncio
    async def test_generate_event_result_no_truncate_long_description(self, agent, mock_router):
        long_desc = "这是一个非常详细的描述，" * 100
        mock_router._mock_tool_args = {"description": long_desc, "context_summary": "摘要", "duration_minutes": 5}

        result = await agent.generate_event_result(EventContext(
            character_name="小雨", character_description="温柔的少女",
            world="", scenario="", recent_diaries=[], today_events=[],
            current_time=datetime(2024, 1, 1, 10, 0)))
        assert len(result.description) > 10

    @pytest.mark.asyncio
    async def test_generate_event_result_empty_description(self, agent, mock_router):
        mock_router._mock_tool_args = {"description": "", "context_summary": "摘要", "duration_minutes": 0}

        result = await agent.generate_event_result(EventContext(
            character_name="小雨", character_description="温柔的少女",
            world="", scenario="", recent_diaries=[], today_events=[],
            current_time=datetime(2024, 1, 1, 10, 0)))
        assert "休息" in result.description

    @pytest.mark.asyncio
    async def test_generate_event_result_no_tool_call_fallback(self, agent, mock_router):
        """LLM 未调用工具 → fallback 返回默认事件"""
        # _mock_tool_args 保持 None → 模拟 LLM 未调用工具
        result = await agent.generate_event_result(EventContext(
            character_name="小雨", character_description="温柔的少女",
            world="", scenario="", recent_diaries=[], today_events=[],
            current_time=datetime(2024, 1, 1, 10, 0)))
        assert "休息" in result.description

    @pytest.mark.asyncio
    async def test_generate_event_result_includes_date_in_prompt(self, agent, mock_router):
        """事件 prompt 必须包含真实日期，防止 LLM 错误推断月份（B-260522-8859d5）"""
        mock_router._mock_tool_args = {"description": "窗外下雨了", "context_summary": "窗外下雨", "duration_minutes": 30}

        await agent.generate_event_result(EventContext(
            character_name="小雨", character_description="温柔的少女",
            world="", scenario="", recent_diaries=[], today_events=[],
            current_time=datetime(2024, 1, 1, 10, 0)))

        user_msg = mock_router.run_via_loop.call_args.kwargs["messages"][1]["content"]
        assert "当前日期: 2024年01月01日" in user_msg
        assert "当前时间: 10:00" in user_msg


class TestGenerateEventReaction:
    @pytest.fixture
    def mock_router(self):
        return _make_router()

    @pytest.fixture
    def agent(self, mock_router):
        config = MagicMock()
        config.background_llm_timeout_seconds = 90
        config.background_llm_max_tool_rounds = 3
        return EventGenerationAgent(mock_router, _make_tool_registry(), config=config)

    @pytest.mark.asyncio
    async def test_generate_event_reaction_success(self, agent, mock_router):
        mock_router._mock_tool_args = {"reaction": "下雨了，有点冷。", "share_desire": 0.3, "follow_up_action": None, "pending_plan": None}

        result = await agent.generate_event_reaction(
            event="窗外下雨了", character_name="小雨",
            character_description="温柔的少女", share_policy="optional")

        assert result.reaction == "下雨了，有点冷。"
        assert result.share_desire == 0.3

    @pytest.mark.asyncio
    async def test_generate_event_reaction_clamp_share_desire_max(self, agent, mock_router):
        mock_router._mock_tool_args = {"reaction": "很棒！", "share_desire": 2.5}

        result = await agent.generate_event_reaction(
            event="赢了比赛", character_name="角色",
            character_description="描述", share_policy="optional")
        assert result.share_desire == 1.0

    @pytest.mark.asyncio
    async def test_generate_event_reaction_clamp_share_desire_min(self, agent, mock_router):
        mock_router._mock_tool_args = {"reaction": "哦。", "share_desire": -1.0}

        result = await agent.generate_event_reaction(
            event="无事发生", character_name="角色",
            character_description="描述", share_policy="optional")
        assert result.share_desire == 0.0

    @pytest.mark.asyncio
    async def test_generate_event_reaction_truncate_long(self, agent, mock_router):
        long_reaction = "这件事真是太" * 30
        mock_router._mock_tool_args = {"reaction": long_reaction, "share_desire": 0.5}

        result = await agent.generate_event_reaction(
            event="事件", character_name="角色",
            character_description="描述", share_policy="optional")
        assert len(result.reaction) <= 80

    @pytest.mark.asyncio
    async def test_generate_event_reaction_fallback(self, agent, mock_router):
        mock_router._mock_error = Exception("error")
        result = await agent.generate_event_reaction(
            event="事件", character_name="角色",
            character_description="描述", share_policy="required")
        assert result.share_desire == 1.0

    @pytest.mark.asyncio
    async def test_share_message_prompt_injection(self, agent, mock_router):
        """确认事件 prompt 包含状态 scale prompt"""
        mock_router._mock_tool_args = {"reaction": "无语", "share_desire": 0.0}

        await agent.generate_event_reaction(
            event="事件", character_name="角色",
            character_description="描述", energy=80, mood=70, health=90)
        call_kwargs = mock_router.run_via_loop.call_args.kwargs
        system_prompt = call_kwargs["messages"][0]["content"]
        assert "你当前的状态" in system_prompt
        assert "体力" in system_prompt
        assert "心情" in system_prompt


@pytest.mark.asyncio
async def test_executor_wiring_agent_loop_layer():
    """验证 AgentLoop → life_collecting_executor → ToolContext.collected_args 接线"""
    from plugins.DicePP.module.persona.llm.providers.protocol import LLMResponse, TokenUsage, ToolCall
    from plugins.DicePP.module.persona.tools.context import ToolContext
    from plugins.DicePP.module.persona.tools.registry import ToolDomain
    from plugins.DicePP.module.persona.tools.collecting import RECORD_EVENT_TOOL
    from plugins.DicePP.module.persona.llm.loop import AgentLoop
    from conftest import make_mock_provider, _make_tool_registry

    tool_registry = _make_tool_registry()
    collected: list = []
    tool_ctx = ToolContext(collected_args=collected)

    provider = make_mock_provider()
    provider.generate = AsyncMock(return_value=LLMResponse(
        content="",
        tool_calls=[ToolCall(id="tc_1", name="record_event",
                    arguments='{"description":"test","context_summary":"sum","duration_minutes":5}')],
        usage=TokenUsage(), finish_reason="tool_calls", model="test-model",
    ))

    loop = AgentLoop(provider=provider, tool_registry=tool_registry, max_tool_rounds=1, max_round_callbacks=0)
    tools = [RECORD_EVENT_TOOL.to_openai_format()]
    await loop.run(
        messages=[{"role": "user", "content": "generate"}],
        tools=tools,
        tool_domains=[ToolDomain.LIFE],
        tool_ctx=tool_ctx,
    )

    assert len(collected) == 1
    assert collected[0]["description"] == "test"
    assert collected[0]["duration_minutes"] == 5
