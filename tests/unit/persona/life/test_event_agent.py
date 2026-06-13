"""
单元测试: Persona Event Agent（使用 AgentRuntime）
"""
import json
import pytest
from datetime import datetime
from unittest.mock import MagicMock, AsyncMock

from plugins.DicePP.module.persona.life.event_agent import (
    EventGenerationAgent, EventContext, EventGenerationResult, EventReactionResult,
)
from conftest import _make_tool_registry, make_mock_runtime


def _make_router():
    router = MagicMock()
    router.data_store = MagicMock()
    router.quota_check_enabled = False
    router.daily_limit = 20
    router.trace_enabled = False
    router.trace_max_age_days = 7
    router.config = MagicMock()
    router.config.timezone = "Asia/Shanghai"
    router._pending_tool_args = None
    router._pending_final_output = "ok"
    return router


class TestGenerateDiary:
    @pytest.fixture
    def mock_router(self):
        return _make_router()

    @pytest.fixture
    def agent(self, mock_router, monkeypatch):
        make_mock_runtime(monkeypatch)
        config = MagicMock()
        config.background_llm_timeout_seconds = 90
        config.background_llm_max_tool_rounds = 3
        return EventGenerationAgent(mock_router, _make_tool_registry(), config=config, store=mock_router.data_store)

    @pytest.mark.asyncio
    async def test_generate_diary_success(self, agent, mock_router):
        mock_router._pending_tool_args = {"diary": "今天过得真充实，发生了很多有趣的事情。"}

        result = await agent.generate_diary(
            events=[{"description": "早上喝咖啡", "reaction": "感觉很清醒"}],
            character_name="测试角色", character_description="一个喜欢记录生活的人",
            yesterday_diary="昨天也很充实。")

        assert result == "今天过得真充实，发生了很多有趣的事情。"

    @pytest.mark.asyncio
    async def test_generate_diary_truncate_long(self, agent, mock_router):
        long_diary = "今天真是漫长的一天" * 60
        mock_router._pending_tool_args = {"diary": long_diary}

        result = await agent.generate_diary(
            events=[{"description": "事件", "reaction": "反应"}],
            character_name="角色", character_description="描述")

        assert len(result) <= 300
        assert result.endswith("...")

    @pytest.mark.asyncio
    async def test_generate_diary_fallback_on_exception(self, agent, mock_router, monkeypatch):
        from plugins.DicePP.module.persona.agent.runtime import AgentRuntime

        async def failing_run(self, messages, user_id, group_id, tool_registry, **kwargs):
            raise Exception("服务不可用")

        monkeypatch.setattr(AgentRuntime, "run", failing_run)
        result = await agent.generate_diary(
            events=[{"description": "事件", "reaction": "反应"}],
            character_name="角色", character_description="描述")
        assert isinstance(result, str)
        assert result.strip()

    @pytest.mark.asyncio
    async def test_generate_diary_no_collected(self, agent, mock_router):
        # _pending_tool_args 保持 None → AgentRuntime.run 不写入 collected
        result = await agent.generate_diary(
            events=[{"description": "事件", "reaction": "反应"}],
            character_name="角色", character_description="描述")
        assert "太累了" in result

    @pytest.mark.asyncio
    async def test_generate_diary_without_yesterday(self, agent, mock_router, monkeypatch):
        mock_router._pending_tool_args = {"diary": "今天的日记内容"}
        from plugins.DicePP.module.persona.agent.runtime import AgentRuntime
        mock_run = AsyncMock(side_effect=AgentRuntime.run)
        monkeypatch.setattr(AgentRuntime, "run", mock_run)
        # 需要让真实 mock_run 也触收集，但这里只用验证 called
        mock_router._pending_tool_args = {"diary": "today's diary"}
        # 先用 simple mock 来验证 called
        await agent.generate_diary(
            events=[{"description": "事件1", "reaction": "反应1"}],
            character_name="角色", character_description="描述", yesterday_diary=None)
        mock_run.assert_called_once()

    @pytest.mark.asyncio
    async def test_generate_diary_with_yesterday(self, agent, mock_router):
        mock_router._pending_tool_args = {"diary": "今天的日记内容"}
        result = await agent.generate_diary(
            events=[{"description": "事件1", "reaction": "反应1"}],
            character_name="角色", character_description="描述",
            yesterday_diary="这是昨天的日记内容，写了很多字。")
        assert result == "今天的日记内容"

    @pytest.mark.asyncio
    async def test_generate_diary_empty_events(self, agent, mock_router):
        mock_router._pending_tool_args = {"diary": "今天没什么特别的事发生。"}
        result = await agent.generate_diary(events=[], character_name="角色", character_description="描述")
        assert result == "今天没什么特别的事发生。"

    @pytest.mark.asyncio
    async def test_generate_diary_includes_date_in_prompt(self, agent, mock_router, monkeypatch):
        """日记 prompt 必须包含真实日期，防止 LLM 错误推断月份（B-260522-8859d5）"""
        from plugins.DicePP.module.persona.agent.runtime import AgentRuntime
        mock_run = AsyncMock()
        monkeypatch.setattr(AgentRuntime, "run", mock_run)

        fake_now = datetime(2026, 5, 23, 14, 30, 0)
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.life.event_agent.wall_now",
            lambda tz: fake_now,
        )

        await agent.generate_diary(
            events=[{"description": "事件", "reaction": "反应"}],
            character_name="角色", character_description="描述")

        user_msg = mock_run.call_args.kwargs["messages"][1]["content"]
        assert "当前日期: 2026年05月23日" in user_msg


class TestStateScalePrompt:
    """测试 _STATE_SCALE_PROMPT 格式"""

    def test_energy_has_behavioral_guidance(self):
        """体力区间包含行为倾向"""
        prompt = EventGenerationAgent._STATE_SCALE_PROMPT
        assert "体力（0-100）影响角色能做什么" in prompt
        assert "可承担消耗较大的活动" in prompt
        assert "自然回避高强度劳作" in prompt
        assert "倾向低消耗室内活动" in prompt
        assert "应选择就近休息、静养、缓慢整理等恢复性行为" in prompt

    def test_mood_no_behavioral_commands(self):
        """心情区间无行为指令（保留描述）"""
        prompt = EventGenerationAgent._STATE_SCALE_PROMPT
        mood_section_start = prompt.index("心情（0-100）")
        health_section_start = prompt.index("健康（0-100）")
        mood_section = prompt[mood_section_start:health_section_start]
        # 不应包含行为指令关键词
        assert "应选择" not in mood_section
        assert "倾向" not in mood_section
        assert "可承担" not in mood_section

    def test_health_no_behavioral_commands(self):
        """健康区间无行为指令（保留描述）"""
        prompt = EventGenerationAgent._STATE_SCALE_PROMPT
        health_section_start = prompt.index("健康（0-100）")
        amplitude_start = prompt.index("单事件状态变化幅度")
        health_section = prompt[health_section_start:amplitude_start]
        assert "应选择" not in health_section
        assert "倾向" not in health_section
        assert "可承担" not in health_section

    def test_wake_up_recovery_rule_present(self):
        """wake_up 恢复规则存在于 slot_type_hint 中"""
        hint = EventGenerationAgent._slot_type_hint("wake_up")
        assert "wake_up" in hint
        assert "保底 +20" in hint
        # 通用 prompt 中不应包含 wake_up 规则
        prompt = EventGenerationAgent._STATE_SCALE_PROMPT
        assert "wake_up 事件特殊规则" not in prompt

    def test_amplitude_reference_present(self):
        """幅度参考存在（简洁形式）"""
        prompt = EventGenerationAgent._STATE_SCALE_PROMPT
        assert "单事件状态变化幅度" in prompt
        assert "±20" in prompt
        # 旧版详细分级参考不应恢复
        assert "±1-5: 轻微变化" not in prompt
        assert "±6-10: 明显变化" not in prompt


class TestGenerateEventResult:
    @pytest.fixture
    def mock_router(self):
        return _make_router()

    @pytest.fixture
    def agent(self, mock_router, monkeypatch):
        make_mock_runtime(monkeypatch)
        config = MagicMock()
        config.background_llm_timeout_seconds = 90
        config.background_llm_max_tool_rounds = 3
        return EventGenerationAgent(mock_router, _make_tool_registry(), config=config, store=mock_router.data_store)

    @pytest.mark.asyncio
    async def test_generate_event_result_success(self, agent, mock_router):
        mock_router._pending_tool_args = {"description": "窗外下雨了", "context_summary": "窗外下雨", "duration_minutes": 30}

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
        mock_router._pending_tool_args = {"description": "事件", "context_summary": "摘要", "duration_minutes": 99999}

        result = await agent.generate_event_result(EventContext(
            character_name="小雨", character_description="温柔的少女",
            world="", scenario="", recent_diaries=[], today_events=[],
            current_time=datetime(2024, 1, 1, 10, 0)))

        assert result.duration_minutes == 2880

    @pytest.mark.asyncio
    async def test_generate_event_result_fallback(self, agent, mock_router, monkeypatch):
        from plugins.DicePP.module.persona.agent.runtime import AgentRuntime

        async def failing_run(self, messages, user_id, group_id, tool_registry, **kwargs):
            raise Exception("forced error")

        monkeypatch.setattr(AgentRuntime, "run", failing_run)
        result = await agent.generate_event_result(EventContext(
            character_name="小雨", character_description="温柔的少女",
            world="", scenario="", recent_diaries=[], today_events=[],
            current_time=datetime(2024, 1, 1, 10, 0)))
        assert "休息" in result.description

    @pytest.mark.asyncio
    async def test_generate_event_result_no_truncate_long_description(self, agent, mock_router):
        long_desc = "这是一个非常详细的描述，" * 100
        mock_router._pending_tool_args = {"description": long_desc, "context_summary": "摘要", "duration_minutes": 5}

        result = await agent.generate_event_result(EventContext(
            character_name="小雨", character_description="温柔的少女",
            world="", scenario="", recent_diaries=[], today_events=[],
            current_time=datetime(2024, 1, 1, 10, 0)))
        assert result.description == long_desc

    @pytest.mark.asyncio
    async def test_generate_event_result_empty_description(self, agent, mock_router):
        mock_router._pending_tool_args = {"description": "", "context_summary": "摘要", "duration_minutes": 0}

        result = await agent.generate_event_result(EventContext(
            character_name="小雨", character_description="温柔的少女",
            world="", scenario="", recent_diaries=[], today_events=[],
            current_time=datetime(2024, 1, 1, 10, 0)))
        assert "休息" in result.description

    @pytest.mark.asyncio
    async def test_generate_event_result_no_tool_call_fallback(self, agent, mock_router):
        """LLM 未调用工具 → fallback 返回默认事件"""
        # _pending_tool_args 保持 None → 模拟 LLM 未调用工具
        result = await agent.generate_event_result(EventContext(
            character_name="小雨", character_description="温柔的少女",
            world="", scenario="", recent_diaries=[], today_events=[],
            current_time=datetime(2024, 1, 1, 10, 0)))
        assert "休息" in result.description

    @pytest.mark.asyncio
    async def test_generate_event_result_includes_date_in_prompt(self, agent, mock_router, monkeypatch):
        """事件 prompt 必须包含真实日期，防止 LLM 错误推断月份（B-260522-8859d5）"""
        from plugins.DicePP.module.persona.agent.runtime import AgentRuntime
        mock_run = AsyncMock()
        monkeypatch.setattr(AgentRuntime, "run", mock_run)

        await agent.generate_event_result(EventContext(
            character_name="小雨", character_description="温柔的少女",
            world="", scenario="", recent_diaries=[], today_events=[],
            current_time=datetime(2024, 1, 1, 10, 0)))

        user_msg = mock_run.call_args.kwargs["messages"][1]["content"]
        assert "当前日期: 2024年01月01日" in user_msg
        assert "当前时间: 10:00" in user_msg

    # ── slot_type 注入测试 ──

    @pytest.mark.asyncio
    async def test_slot_type_wake_up_injects_scene_label(self, agent, mock_router, monkeypatch):
        """wake_up → 场景标注注入"""
        from plugins.DicePP.module.persona.agent.runtime import AgentRuntime
        mock_run = AsyncMock()
        monkeypatch.setattr(AgentRuntime, "run", mock_run)

        await agent.generate_event_result(EventContext(
            character_name="小雨", character_description="温柔的少女",
            world="", scenario="", recent_diaries=[], today_events=[],
            current_time=datetime(2024, 1, 1, 8, 0),
            slot_type="wake_up"))

        system_prompt = mock_run.call_args.kwargs["messages"][0]["content"]
        assert "当前事件类型: wake_up" in system_prompt
        assert "角色刚刚醒来" in system_prompt

    @pytest.mark.asyncio
    async def test_slot_type_good_night_injects_scene_label(self, agent, mock_router, monkeypatch):
        """good_night → 场景标注注入"""
        from plugins.DicePP.module.persona.agent.runtime import AgentRuntime
        mock_run = AsyncMock()
        monkeypatch.setattr(AgentRuntime, "run", mock_run)

        await agent.generate_event_result(EventContext(
            character_name="小雨", character_description="温柔的少女",
            world="", scenario="", recent_diaries=[], today_events=[],
            current_time=datetime(2024, 1, 1, 22, 0),
            slot_type="good_night"))

        system_prompt = mock_run.call_args.kwargs["messages"][0]["content"]
        assert "当前事件类型: good_night" in system_prompt
        assert "角色准备入睡" in system_prompt

    @pytest.mark.asyncio
    async def test_slot_type_system_no_injection(self, agent, mock_router, monkeypatch):
        """system → 无注入"""
        from plugins.DicePP.module.persona.agent.runtime import AgentRuntime
        mock_run = AsyncMock()
        monkeypatch.setattr(AgentRuntime, "run", mock_run)

        await agent.generate_event_result(EventContext(
            character_name="小雨", character_description="温柔的少女",
            world="", scenario="", recent_diaries=[], today_events=[],
            current_time=datetime(2024, 1, 1, 10, 0),
            slot_type="system"))

        system_prompt = mock_run.call_args.kwargs["messages"][0]["content"]
        assert "当前事件类型:" not in system_prompt

    @pytest.mark.asyncio
    async def test_slot_type_defaults_to_system(self, agent, mock_router, monkeypatch):
        """未传 slot_type 时默认 "system"，无场景标注注入"""
        from plugins.DicePP.module.persona.agent.runtime import AgentRuntime
        mock_run = AsyncMock()
        monkeypatch.setattr(AgentRuntime, "run", mock_run)

        # 不传 slot_type，依赖默认值
        context = EventContext(
            character_name="小雨", character_description="温柔的少女",
            world="", scenario="", recent_diaries=[], today_events=[],
            current_time=datetime(2024, 1, 1, 10, 0))
        assert context.slot_type == "system"

        await agent.generate_event_result(context)
        system_prompt = mock_run.call_args.kwargs["messages"][0]["content"]
        assert "当前事件类型:" not in system_prompt


class TestGenerateEventReaction:
    @pytest.fixture
    def mock_router(self):
        return _make_router()

    @pytest.fixture
    def agent(self, mock_router, monkeypatch):
        make_mock_runtime(monkeypatch)
        config = MagicMock()
        config.background_llm_timeout_seconds = 90
        config.background_llm_max_tool_rounds = 3
        return EventGenerationAgent(mock_router, _make_tool_registry(), config=config, store=mock_router.data_store)

    @pytest.mark.asyncio
    async def test_generate_event_reaction_success(self, agent, mock_router):
        mock_router._pending_tool_args = {"reaction": "下雨了，有点冷。", "share_desire": 0.3, "follow_up_action": None, "pending_plan": None}

        result = await agent.generate_event_reaction(
            event="窗外下雨了", character_name="小雨",
            character_description="温柔的少女", share_policy="optional")

        assert result.reaction == "下雨了，有点冷。"
        assert result.share_desire == 0.3

    @pytest.mark.asyncio
    async def test_generate_event_reaction_clamp_share_desire_max(self, agent, mock_router):
        mock_router._pending_tool_args = {"reaction": "很棒！", "share_desire": 2.5}

        result = await agent.generate_event_reaction(
            event="赢了比赛", character_name="角色",
            character_description="描述", share_policy="optional")
        assert result.share_desire == 1.0

    @pytest.mark.asyncio
    async def test_generate_event_reaction_clamp_share_desire_min(self, agent, mock_router):
        mock_router._pending_tool_args = {"reaction": "哦。", "share_desire": -1.0}

        result = await agent.generate_event_reaction(
            event="无事发生", character_name="角色",
            character_description="描述", share_policy="optional")
        assert result.share_desire == 0.0

    @pytest.mark.asyncio
    async def test_generate_event_reaction_truncate_long(self, agent, mock_router):
        long_reaction = "这件事真是太" * 30
        mock_router._pending_tool_args = {"reaction": long_reaction, "share_desire": 0.5}

        result = await agent.generate_event_reaction(
            event="事件", character_name="角色",
            character_description="描述", share_policy="optional")
        assert len(result.reaction) <= 80

    @pytest.mark.asyncio
    async def test_generate_event_reaction_fallback(self, agent, mock_router, monkeypatch):
        from plugins.DicePP.module.persona.agent.runtime import AgentRuntime

        async def failing_run(self, messages, user_id, group_id, tool_registry, **kwargs):
            raise Exception("error")

        monkeypatch.setattr(AgentRuntime, "run", failing_run)
        result = await agent.generate_event_reaction(
            event="事件", character_name="角色",
            character_description="描述", share_policy="required")
        assert result.share_desire == 1.0

    @pytest.mark.asyncio
    async def test_share_message_prompt_injection(self, agent, mock_router, monkeypatch):
        """确认事件 prompt 包含状态 scale prompt"""
        from plugins.DicePP.module.persona.agent.runtime import AgentRuntime
        mock_run = AsyncMock()
        monkeypatch.setattr(AgentRuntime, "run", mock_run)

        await agent.generate_event_reaction(
            event="事件", character_name="角色",
            character_description="描述", energy=80, mood=70, health=90)
        call_kwargs = mock_run.call_args.kwargs
        system_prompt = call_kwargs["messages"][0]["content"]
        assert "你当前的状态" in system_prompt
        assert "体力" in system_prompt
        assert "心情" in system_prompt
