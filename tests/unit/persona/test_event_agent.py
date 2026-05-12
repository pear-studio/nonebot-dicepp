"""
单元测试: Persona Event Agent

测试 EventGenerationAgent 的主要方法
"""

import json
import pytest

from datetime import datetime
from unittest.mock import MagicMock, AsyncMock


from plugins.DicePP.module.persona.life.event_agent import (
    EventGenerationAgent,
    EventContext,
    EventGenerationResult,
    EventReactionResult,
)
from plugins.DicePP.module.persona.data.models import ModelTier


def _make_side_effect(result_args: str, tool_name: str):
    """创建 router.generate 的 side_effect，调用 tool_executor 填充 CollectExecutor"""
    async def side_effect(**kwargs):
        tool_executor = kwargs.get("tool_executor")
        if tool_executor:
            tc = {
                "id": "tc_1",
                "name": tool_name,
                "arguments": result_args,
            }
            await tool_executor([tc])
        return "", {}
    return side_effect


class TestGenerateDiary:
    """测试 generate_diary 方法"""

    @pytest.fixture
    def mock_llm_router(self):
        router = MagicMock()
        router.generate = AsyncMock()
        return router

    @pytest.fixture
    def agent(self, mock_llm_router):
        config = MagicMock()
        config.background_llm_timeout_seconds = 90
        config.background_llm_max_tool_rounds = 3
        return EventGenerationAgent(mock_llm_router, config=config)

    @pytest.mark.asyncio
    async def test_generate_diary_success(self, agent, mock_llm_router):
        """正常生成日记"""
        mock_llm_router.generate.side_effect = _make_side_effect(
            '{"diary": "今天过得真充实，发生了很多有趣的事情。"}',
            "record_diary_entry",
        )

        events = [
            {"description": "早上喝咖啡", "reaction": "感觉很清醒"},
            {"description": "下午散步", "reaction": "心情放松了许多"},
        ]

        result = await agent.generate_diary(
            events=events,
            character_name="测试角色",
            character_description="一个喜欢记录生活的人",
            yesterday_diary="昨天也很充实。",
        )

        assert result == "今天过得真充实，发生了很多有趣的事情。"
        mock_llm_router.generate.assert_called_once()
        call_kwargs = mock_llm_router.generate.call_args.kwargs
        assert call_kwargs["temperature"] == 0.85
        assert call_kwargs["timeout"] == 90
        assert call_kwargs["model_tier"] == ModelTier.AUXILIARY
        assert call_kwargs["tools"] is not None

    @pytest.mark.asyncio
    async def test_generate_diary_truncate_long(self, agent, mock_llm_router):
        """超长日记被截断"""
        long_diary = "今天真是漫长的一天" * 60
        mock_llm_router.generate.side_effect = _make_side_effect(
            f'{{"diary": "{long_diary}"}}',
            "record_diary_entry",
        )

        result = await agent.generate_diary(
            events=[{"description": "事件", "reaction": "反应"}],
            character_name="角色",
            character_description="描述",
        )

        assert len(result) <= 300
        assert result.endswith("...")

    @pytest.mark.asyncio
    async def test_generate_diary_fallback_on_exception(self, agent, mock_llm_router):
        """异常时返回默认兜底文本"""
        mock_llm_router.generate.side_effect = Exception("服务不可用")

        result = await agent.generate_diary(
            events=[{"description": "事件", "reaction": "反应"}],
            character_name="角色",
            character_description="描述",
        )

        assert "太累了" in result
        assert "简单记录" in result

    @pytest.mark.asyncio
    async def test_generate_diary_no_collected(self, agent, mock_llm_router):
        """LLM 未调用工具时返回兜底文本"""
        mock_llm_router.generate.return_value = ("text without tool call", {})

        result = await agent.generate_diary(
            events=[{"description": "事件", "reaction": "反应"}],
            character_name="角色",
            character_description="描述",
        )

        assert "太累了" in result

    @pytest.mark.asyncio
    async def test_generate_diary_without_yesterday(self, agent, mock_llm_router):
        """不传入昨天日记的情况"""
        mock_llm_router.generate.side_effect = _make_side_effect(
            '{"diary": "今天的日记内容"}',
            "record_diary_entry",
        )

        events = [{"description": "事件1", "reaction": "反应1"}]
        await agent.generate_diary(
            events=events,
            character_name="角色",
            character_description="描述",
            yesterday_diary=None,
        )

        call_args = mock_llm_router.generate.call_args.kwargs
        messages = call_args["messages"]
        user_prompt = messages[1]["content"]
        assert "昨天的日记" not in user_prompt
        assert "事件1" in user_prompt
        assert "反应1" in user_prompt

    @pytest.mark.asyncio
    async def test_generate_diary_with_yesterday(self, agent, mock_llm_router):
        """传入昨天日记的情况"""
        mock_llm_router.generate.side_effect = _make_side_effect(
            '{"diary": "今天的日记内容"}',
            "record_diary_entry",
        )

        events = [{"description": "事件1", "reaction": "反应1"}]
        yesterday = "这是昨天的日记内容，写了很多字。"
        await agent.generate_diary(
            events=events,
            character_name="角色",
            character_description="描述",
            yesterday_diary=yesterday,
        )

        call_args = mock_llm_router.generate.call_args.kwargs
        messages = call_args["messages"]
        user_prompt = messages[1]["content"]
        assert "昨天的日记" in user_prompt
        assert yesterday[:200] in user_prompt

    @pytest.mark.asyncio
    async def test_generate_diary_empty_events(self, agent, mock_llm_router):
        """空事件列表"""
        mock_llm_router.generate.side_effect = _make_side_effect(
            '{"diary": "今天没什么特别的事发生。"}',
            "record_diary_entry",
        )

        result = await agent.generate_diary(
            events=[],
            character_name="角色",
            character_description="描述",
        )

        assert result == "今天没什么特别的事发生。"


class TestGenerateEventResult:
    """测试 generate_event_result 工具路径"""

    @pytest.fixture
    def mock_llm_router(self):
        router = MagicMock()
        router.generate = AsyncMock()
        return router

    @pytest.fixture
    def agent(self, mock_llm_router):
        config = MagicMock()
        config.background_llm_timeout_seconds = 90
        config.background_llm_max_tool_rounds = 3
        return EventGenerationAgent(mock_llm_router, config=config)

    @pytest.mark.asyncio
    async def test_generate_event_result_success(self, agent, mock_llm_router):
        mock_llm_router.generate.side_effect = _make_side_effect(
            '{"description": "窗外下雨了", "context_summary": "窗外下雨", "duration_minutes": 30}',
            "record_event",
        )

        result = await agent.generate_event_result(
            EventContext(
                character_name="小雨",
                character_description="温柔的少女",
                world="",
                scenario="",
                recent_diaries=[],
                today_events=[],
                current_time=datetime(2024, 1, 1, 10, 0),
            )
        )

        assert result.description == "窗外下雨了"
        assert result.context_summary == "窗外下雨"
        assert result.duration_minutes == 30
        assert result.raw_response != ""
        raw = json.loads(result.raw_response)
        assert raw["description"] == "窗外下雨了"
        assert raw["context_summary"] == "窗外下雨"
        assert raw["duration_minutes"] == 30
        assert result.system_prompt_digest != ""
        assert "世界观设定专家" in result.system_prompt_digest
        mock_llm_router.generate.assert_called_once()
        call_kwargs = mock_llm_router.generate.call_args.kwargs
        assert call_kwargs["timeout"] == 90

    @pytest.mark.asyncio
    async def test_generate_event_result_fallback(self, agent, mock_llm_router):
        mock_llm_router.generate.side_effect = Exception("forced tool error")

        result = await agent.generate_event_result(
            EventContext(
                character_name="小雨",
                character_description="温柔的少女",
                world="",
                scenario="",
                recent_diaries=[],
                today_events=[],
                current_time=datetime(2024, 1, 1, 10, 0),
            )
        )

        assert "休息" in result.description
        assert result.duration_minutes == 0
        assert result.energy_delta == 0
        assert result.mood_delta is None
        assert result.health_delta is None
        assert result.raw_response != ""
        assert "fallback" in result.system_prompt_digest

    @pytest.mark.asyncio
    async def test_generate_event_result_clamp_duration_max(self, agent, mock_llm_router):
        mock_llm_router.generate.side_effect = _make_side_effect(
            '{"description": "测试中", "duration_minutes": 3000}',
            "record_event",
        )

        result = await agent.generate_event_result(
            EventContext(
                character_name="小雨",
                character_description="温柔的少女",
                world="",
                scenario="",
                recent_diaries=[],
                today_events=[],
                current_time=datetime(2024, 1, 1, 10, 0),
            )
        )

        assert result.duration_minutes == 2880

    @pytest.mark.asyncio
    async def test_generate_event_result_clamp_duration_min(self, agent, mock_llm_router):
        mock_llm_router.generate.side_effect = _make_side_effect(
            '{"description": "测试中", "duration_minutes": -10}',
            "record_event",
        )

        result = await agent.generate_event_result(
            EventContext(
                character_name="小雨",
                character_description="温柔的少女",
                world="",
                scenario="",
                recent_diaries=[],
                today_events=[],
                current_time=datetime(2024, 1, 1, 10, 0),
            )
        )

        assert result.duration_minutes == 0

    @pytest.mark.asyncio
    async def test_generate_event_result_empty_description_fallback(self, agent, mock_llm_router):
        mock_llm_router.generate.side_effect = _make_side_effect(
            '{"description": "", "duration_minutes": 0}',
            "record_event",
        )

        result = await agent.generate_event_result(
            EventContext(
                character_name="小雨",
                character_description="温柔的少女",
                world="",
                scenario="",
                recent_diaries=[],
                today_events=[],
                current_time=datetime(2024, 1, 1, 10, 0),
            )
        )

        assert "休息" in result.description
        assert result.duration_minutes == 0

    @pytest.mark.asyncio
    async def test_generate_event_result_no_truncate_long_description(self, agent, mock_llm_router):
        """description 不再硬截断，完整保留；context_summary 为空时 fallback 到 description 前 60 字"""
        long_desc = "窗外" * 50
        mock_llm_router.generate.side_effect = _make_side_effect(
            f'{{"description": "{long_desc}", "duration_minutes": 0}}',
            "record_event",
        )

        result = await agent.generate_event_result(
            EventContext(
                character_name="小雨",
                character_description="温柔的少女",
                world="",
                scenario="",
                recent_diaries=[],
                today_events=[],
                current_time=datetime(2024, 1, 1, 10, 0),
            )
        )

        assert result.description == long_desc
        assert len(result.context_summary) == 60
        assert result.context_summary == long_desc[:60]

        short_desc = "窗外下雨了"
        mock_llm_router.generate.side_effect = _make_side_effect(
            f'{{"description": "{short_desc}", "duration_minutes": 0}}',
            "record_event",
        )
        result2 = await agent.generate_event_result(
            EventContext(
                character_name="小雨",
                character_description="温柔的少女",
                world="",
                scenario="",
                recent_diaries=[],
                today_events=[],
                current_time=datetime(2024, 1, 1, 10, 0),
            )
        )
        assert result2.context_summary == short_desc

    @pytest.mark.asyncio
    async def test_generate_event_result_empty_context(self, agent, mock_llm_router):
        """空上下文时正常生成"""
        mock_llm_router.generate.side_effect = _make_side_effect(
            '{"description": "正在休息", "duration_minutes": 15}',
            "record_event",
        )

        result = await agent.generate_event_result(
            EventContext(
                character_name="小雨",
                character_description="温柔的少女",
                world="",
                scenario="",
                recent_diaries=[],
                today_events=[],
                current_time=datetime(2024, 1, 1, 10, 0),
            )
        )

        assert result.description == "正在休息"
        assert result.duration_minutes == 15


class TestGenerateEventReaction:
    """测试 generate_event_reaction 工具路径"""

    @pytest.fixture
    def mock_llm_router(self):
        router = MagicMock()
        router.generate = AsyncMock()
        return router

    @pytest.fixture
    def agent(self, mock_llm_router):
        config = MagicMock()
        config.background_llm_timeout_seconds = 90
        config.background_llm_max_tool_rounds = 3
        return EventGenerationAgent(mock_llm_router, config=config)

    @pytest.mark.asyncio
    async def test_generate_event_reaction_success(self, agent, mock_llm_router):
        mock_llm_router.generate.side_effect = _make_side_effect(
            '{"reaction": "真开心~", "share_desire": 0.8}',
            "record_reaction",
        )

        result = await agent.generate_event_reaction(
            event="窗外下雨了",
            character_name="小雨",
            character_description="温柔的少女",
        )

        assert result.reaction == "真开心~"
        assert result.share_desire == 0.8
        assert result.raw_response != ""
        raw = json.loads(result.raw_response)
        assert raw["reaction"] == "真开心~"
        assert raw["share_desire"] == 0.8
        mock_llm_router.generate.assert_called_once()
        call_kwargs = mock_llm_router.generate.call_args.kwargs
        assert call_kwargs["timeout"] == 90

    @pytest.mark.asyncio
    async def test_generate_event_reaction_fallback_required(self, agent, mock_llm_router):
        mock_llm_router.generate.side_effect = Exception("tool error")

        result = await agent.generate_event_reaction(
            event="窗外下雨了",
            character_name="小雨",
            character_description="温柔的少女",
            share_policy="required",
        )

        assert result.share_desire == 1.0

    @pytest.mark.asyncio
    async def test_generate_event_reaction_fallback_never(self, agent, mock_llm_router):
        mock_llm_router.generate.side_effect = Exception("tool error")

        result = await agent.generate_event_reaction(
            event="窗外下雨了",
            character_name="小雨",
            character_description="温柔的少女",
            share_policy="never",
        )

        assert result.share_desire == 0.0

    @pytest.mark.asyncio
    async def test_generate_event_reaction_fallback_optional(self, agent, mock_llm_router):
        mock_llm_router.generate.side_effect = Exception("tool error")

        result = await agent.generate_event_reaction(
            event="窗外下雨了",
            character_name="小雨",
            character_description="温柔的少女",
            share_policy="optional",
        )

        assert result.share_desire == 0.5

    @pytest.mark.asyncio
    async def test_generate_event_reaction_clamp_share_desire_max(self, agent, mock_llm_router):
        mock_llm_router.generate.side_effect = _make_side_effect(
            '{"reaction": "开心", "share_desire": 1.5}',
            "record_reaction",
        )

        result = await agent.generate_event_reaction(
            event="窗外下雨了",
            character_name="小雨",
            character_description="温柔的少女",
        )

        assert result.reaction == "开心"
        assert result.share_desire == 1.0

    @pytest.mark.asyncio
    async def test_generate_event_reaction_clamp_share_desire_min(self, agent, mock_llm_router):
        mock_llm_router.generate.side_effect = _make_side_effect(
            '{"reaction": "开心", "share_desire": -0.3}',
            "record_reaction",
        )

        result = await agent.generate_event_reaction(
            event="窗外下雨了",
            character_name="小雨",
            character_description="温柔的少女",
        )

        assert result.reaction == "开心"
        assert result.share_desire == 0.0

    @pytest.mark.asyncio
    async def test_generate_event_reaction_empty_reaction_fallback(self, agent, mock_llm_router):
        mock_llm_router.generate.side_effect = _make_side_effect(
            '{"reaction": "", "share_desire": 0.5}',
            "record_reaction",
        )

        result = await agent.generate_event_reaction(
            event="窗外下雨了",
            character_name="小雨",
            character_description="温柔的少女",
        )

        assert "小雨" in result.reaction
        assert "默默地想着" in result.reaction
        assert result.share_desire == 0.5

    @pytest.mark.asyncio
    async def test_share_message_prompt_injection(self):
        """验证分享消息 prompt 注入状态/事件/意向"""
        from io import StringIO
        from loguru import logger
        from plugins.DicePP.module.persona.life.event_agent import ShareMessageContext

        mock_router = MagicMock()
        mock_router.generate = AsyncMock()
        mock_router.generate.side_effect = _make_side_effect(
            '{"message": "茶很好喝哦"}',
            "record_share_message",
        )
        agent = EventGenerationAgent(llm_router=mock_router)

        context = ShareMessageContext(
            event_description="泡了茶",
            reaction="很香",
            character_name="测试角色",
            character_description="一个喜欢阅读和咖啡的温柔女孩",
            target_user_id="u1",
            relationship_score=70.0,
            warmth_label="友好",
            user_profile_facts="（无）",
            recent_history="（无）",
            message_type="random_event",
            environment="private",
            energy=60,
            mood=70,
            health=55,
            today_events=[{"description": "泡了茶", "time": "09:00"}],
            current_intention="想喝茶",
        )

        output = StringIO()
        handler_id = logger.add(output, level="DEBUG", format="{message}")
        try:
            message = await agent.generate_share_message(context)
        finally:
            logger.remove(handler_id)

        assert message == "茶很好喝哦"

        logs = output.getvalue()
        assert "[prompt:system_share]" in logs
        assert "[prompt:user_share]" in logs
        assert "体力: 60/100" in logs
        assert "当前惦记的事: 想喝茶" in logs
        assert "泡了茶" in logs


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
