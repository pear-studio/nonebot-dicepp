"""
单元测试: Persona Event Agent

测试 EventGenerationAgent 的三个主要方法:
- generate_event: 生成客观生活事件
- generate_reaction: 生成角色对事件的反应
- generate_diary: 生成日记总结
"""

import pytest

from datetime import datetime
from unittest.mock import MagicMock, AsyncMock


from plugins.DicePP.module.persona.agents.event_agent import (
    EventGenerationAgent,
    EventContext,
    EventGenerationResult,
    EventReactionResult,
)
from plugins.DicePP.module.persona.data.models import ModelTier


class TestEventGenerationAgent:
    """测试事件生成 Agent"""

    @pytest.fixture
    def mock_llm_router(self):
        """创建 Mock LLM Router"""
        router = MagicMock()
        router.generate = AsyncMock()
        return router

    @pytest.fixture
    def agent(self, mock_llm_router):
        """创建 EventGenerationAgent 实例"""
        return EventGenerationAgent(mock_llm_router)

    @pytest.fixture
    def event_context(self):
        """创建测试用的 EventContext"""
        return EventContext(
            character_name="测试角色",
            character_description="一个温柔的AI助手",
            world="现代日常世界",
            scenario="居家生活",
            recent_diaries=["今天天气不错，去公园散步了。"],
            today_events=[{"description": "早上喝了咖啡"}],
            permanent_state="心情愉悦",
            current_time=datetime(2024, 1, 1, 10, 0),
        )

    class TestGenerateDiary:
        """测试 generate_diary 方法"""

        async def test_generate_diary_success(self, agent, mock_llm_router):
            """测试正常生成日记"""
            mock_llm_router.generate.return_value = "  今天过得真充实，发生了很多有趣的事情。  "

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
            assert call_kwargs["model_tier"] == ModelTier.AUXILIARY

        async def test_generate_diary_truncate_long(self, agent, mock_llm_router):
            """测试超长日记被截断"""
            long_diary = "今天真是漫长的一天" * 60  # 540字，超过500字
            mock_llm_router.generate.return_value = long_diary

            result = await agent.generate_diary(
                events=[{"description": "事件", "reaction": "反应"}],
                character_name="角色",
                character_description="描述",
            )

            assert len(result) <= 500
            assert result.endswith("...")

        async def test_generate_diary_fallback_on_exception(self, agent, mock_llm_router):
            """测试异常时返回默认兜底文本"""
            mock_llm_router.generate.side_effect = Exception("服务不可用")

            result = await agent.generate_diary(
                events=[{"description": "事件", "reaction": "反应"}],
                character_name="角色",
                character_description="描述",
            )

            assert "太累了" in result
            assert "简单记录" in result

        async def test_generate_diary_without_yesterday(self, agent, mock_llm_router):
            """测试不传入昨天日记的情况"""
            mock_llm_router.generate.return_value = "今天的日记内容"

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

        async def test_generate_diary_with_yesterday(self, agent, mock_llm_router):
            """测试传入昨天日记的情况"""
            mock_llm_router.generate.return_value = "今天的日记内容"

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

        async def test_generate_diary_empty_events(self, agent, mock_llm_router):
            """测试空事件列表"""
            mock_llm_router.generate.return_value = "今天没什么特别的事发生。"

            result = await agent.generate_diary(
                events=[],
                character_name="角色",
                character_description="描述",
            )

            assert result == "今天没什么特别的事发生。"


class TestGenerateEventResult:
    """测试 generate_event_result Function Calling 路径"""

    @pytest.fixture
    def mock_llm_router_forced(self):
        router = MagicMock()
        router.generate_with_forced_tool = AsyncMock()
        return router

    @pytest.fixture
    def agent_forced(self, mock_llm_router_forced):
        return EventGenerationAgent(mock_llm_router_forced)

    @pytest.mark.asyncio
    async def test_generate_event_result_success(self, agent_forced, mock_llm_router_forced):
        mock_llm_router_forced.generate_with_forced_tool.return_value = (
            '{"description": "窗外下雨了", "duration_minutes": 30}',
            {}
        )

        result = await agent_forced.generate_event_result(
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
        assert result.duration_minutes == 30
        mock_llm_router_forced.generate_with_forced_tool.assert_called_once()

    @pytest.mark.asyncio
    async def test_generate_event_result_fallback(self, agent_forced, mock_llm_router_forced):
        mock_llm_router_forced.generate_with_forced_tool.side_effect = Exception("forced tool error")

        result = await agent_forced.generate_event_result(
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
    async def test_generate_event_result_clamp_duration_max(self, agent_forced, mock_llm_router_forced):
        mock_llm_router_forced.generate_with_forced_tool.return_value = (
            '{"description": "测试中", "duration_minutes": 3000}',
            {}
        )

        result = await agent_forced.generate_event_result(
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
    async def test_generate_event_result_clamp_duration_min(self, agent_forced, mock_llm_router_forced):
        mock_llm_router_forced.generate_with_forced_tool.return_value = (
            '{"description": "测试中", "duration_minutes": -10}',
            {}
        )

        result = await agent_forced.generate_event_result(
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
    async def test_generate_event_result_empty_description_fallback(self, agent_forced, mock_llm_router_forced):
        mock_llm_router_forced.generate_with_forced_tool.return_value = (
            '{"description": "", "duration_minutes": 0}',
            {}
        )

        result = await agent_forced.generate_event_result(
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
    async def test_generate_event_result_truncate_long_description(self, agent_forced, mock_llm_router_forced):
        """超长描述被截断到 _EVENT_DESCRIPTION_MAX_LEN 并加省略号"""
        long_desc = "窗外" * 50  # 100 字，超过 60
        mock_llm_router_forced.generate_with_forced_tool.return_value = (
            f'{{"description": "{long_desc}", "duration_minutes": 0}}',
            {}
        )

        result = await agent_forced.generate_event_result(
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

        assert len(result.description) <= 60
        assert result.description.endswith("...")

    @pytest.mark.asyncio
    async def test_generate_event_result_empty_context(self, agent_forced, mock_llm_router_forced):
        """空上下文（recent_diaries/today_events 均为空）时正常生成"""
        mock_llm_router_forced.generate_with_forced_tool.return_value = (
            '{"description": "正在休息", "duration_minutes": 15}',
            {}
        )

        result = await agent_forced.generate_event_result(
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
    """测试 generate_event_reaction Function Calling 路径"""

    @pytest.fixture
    def mock_llm_router_forced(self):
        router = MagicMock()
        router.generate_with_forced_tool = AsyncMock()
        return router

    @pytest.fixture
    def agent_forced(self, mock_llm_router_forced):
        return EventGenerationAgent(mock_llm_router_forced)

    @pytest.mark.asyncio
    async def test_generate_event_reaction_success(self, agent_forced, mock_llm_router_forced):
        mock_llm_router_forced.generate_with_forced_tool.return_value = (
            '{"reaction": "真开心~", "share_desire": 0.8}',
            {}
        )

        result = await agent_forced.generate_event_reaction(
            event="窗外下雨了",
            character_name="小雨",
            character_description="温柔的少女",
        )

        assert result.reaction == "真开心~"
        assert result.share_desire == 0.8
        mock_llm_router_forced.generate_with_forced_tool.assert_called_once()

    @pytest.mark.asyncio
    async def test_generate_event_reaction_fallback_required(self, agent_forced, mock_llm_router_forced):
        mock_llm_router_forced.generate_with_forced_tool.side_effect = Exception("tool error")

        result = await agent_forced.generate_event_reaction(
            event="窗外下雨了",
            character_name="小雨",
            character_description="温柔的少女",
            share_policy="required",
        )

        assert result.share_desire == 1.0

    @pytest.mark.asyncio
    async def test_generate_event_reaction_fallback_never(self, agent_forced, mock_llm_router_forced):
        mock_llm_router_forced.generate_with_forced_tool.side_effect = Exception("tool error")

        result = await agent_forced.generate_event_reaction(
            event="窗外下雨了",
            character_name="小雨",
            character_description="温柔的少女",
            share_policy="never",
        )

        assert result.share_desire == 0.0

    @pytest.mark.asyncio
    async def test_generate_event_reaction_fallback_optional(self, agent_forced, mock_llm_router_forced):
        mock_llm_router_forced.generate_with_forced_tool.side_effect = Exception("tool error")

        result = await agent_forced.generate_event_reaction(
            event="窗外下雨了",
            character_name="小雨",
            character_description="温柔的少女",
            share_policy="optional",
        )

        assert result.share_desire == 0.5

    @pytest.mark.asyncio
    async def test_generate_event_reaction_clamp_share_desire_max(self, agent_forced, mock_llm_router_forced):
        mock_llm_router_forced.generate_with_forced_tool.return_value = (
            '{"reaction": "开心", "share_desire": 1.5}',
            {}
        )

        result = await agent_forced.generate_event_reaction(
            event="窗外下雨了",
            character_name="小雨",
            character_description="温柔的少女",
        )

        assert result.reaction == "开心"
        assert result.share_desire == 1.0

    @pytest.mark.asyncio
    async def test_generate_event_reaction_clamp_share_desire_min(self, agent_forced, mock_llm_router_forced):
        mock_llm_router_forced.generate_with_forced_tool.return_value = (
            '{"reaction": "开心", "share_desire": -0.3}',
            {}
        )

        result = await agent_forced.generate_event_reaction(
            event="窗外下雨了",
            character_name="小雨",
            character_description="温柔的少女",
        )

        assert result.reaction == "开心"
        assert result.share_desire == 0.0

    @pytest.mark.asyncio
    async def test_generate_event_reaction_empty_reaction_fallback(self, agent_forced, mock_llm_router_forced):
        mock_llm_router_forced.generate_with_forced_tool.return_value = (
            '{"reaction": "", "share_desire": 0.5}',
            {}
        )

        result = await agent_forced.generate_event_reaction(
            event="窗外下雨了",
            character_name="小雨",
            character_description="温柔的少女",
        )

        assert "小雨" in result.reaction
        assert "默默地想着" in result.reaction
        assert result.share_desire == 0.5


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
