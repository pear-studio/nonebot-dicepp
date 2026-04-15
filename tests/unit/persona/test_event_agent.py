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

    class TestGenerateEvent:
        """测试 generate_event 方法"""

        async def test_generate_event_success(self, agent, mock_llm_router, event_context):
            """测试正常生成事件"""
            mock_llm_router.generate.return_value = "  窗外下起了小雨，雨滴轻轻敲打着窗户。  "

            result = await agent.generate_event(event_context)

            # 验证返回值被正确清理（strip）
            assert result == "窗外下起了小雨，雨滴轻轻敲打着窗户。"
            # 验证 LLM 被调用
            mock_llm_router.generate.assert_called_once()
            call_kwargs = mock_llm_router.generate.call_args.kwargs
            # 验证参数
            assert call_kwargs["model_tier"] == ModelTier.AUXILIARY
            assert call_kwargs["temperature"] == 0.8
            assert "messages" in call_kwargs

        async def test_generate_event_strip_quotes(self, agent, mock_llm_router, event_context):
            """测试生成的内容去除引号"""
            mock_llm_router.generate.return_value = '"带引号的事件内容"'

            result = await agent.generate_event(event_context)

            assert result == "带引号的事件内容"
            assert '"' not in result

        async def test_generate_event_strip_single_quotes(self, agent, mock_llm_router, event_context):
            """测试生成的内容去除单引号"""
            mock_llm_router.generate.return_value = "'带单引号的事件内容'"

            result = await agent.generate_event(event_context)

            assert result == "带单引号的事件内容"
            assert "'" not in result

        async def test_generate_event_truncate_long(self, agent, mock_llm_router, event_context):
            """测试超长内容被截断"""
            long_text = "这是一个超长的事件描述" * 10  # 超过100字
            mock_llm_router.generate.return_value = long_text

            result = await agent.generate_event(event_context)

            assert len(result) <= 100
            assert result.endswith("...")

        async def test_generate_event_fallback_on_exception(self, agent, mock_llm_router, event_context):
            """测试异常时返回默认兜底文本"""
            mock_llm_router.generate.side_effect = Exception("LLM 调用失败")

            result = await agent.generate_event(event_context)

            assert "我正在房间里休息。" in result

        async def test_generate_event_with_empty_context(self, agent, mock_llm_router):
            """测试空上下文的处理"""
            context = EventContext(
                character_name="角色A",
                character_description="",
                world="",
                scenario="",
                recent_diaries=[],
                today_events=[],
            )
            mock_llm_router.generate.return_value = "测试事件"

            result = await agent.generate_event(context)

            assert result == "测试事件"
            # 验证 prompt 中包含了默认值
            call_args = mock_llm_router.generate.call_args.kwargs
            messages = call_args["messages"]
            assert "现代日常世界" in messages[0]["content"]  # world 默认值
            assert "日常生活" in messages[0]["content"]  # scenario 默认值

    class TestGenerateReaction:
        """测试 generate_reaction 方法"""

        async def test_generate_reaction_success(self, agent, mock_llm_router):
            """测试正常生成反应"""
            mock_llm_router.generate.return_value = "  这真是一件有趣的事情呢！  "

            result = await agent.generate_reaction(
                event="收到了一份神秘的礼物",
                character_name="苏晓",
                character_description="一个温柔体贴的AI助手",
            )

            assert result == "这真是一件有趣的事情呢！"
            mock_llm_router.generate.assert_called_once()
            call_kwargs = mock_llm_router.generate.call_args.kwargs
            assert call_kwargs["temperature"] == 0.9
            assert call_kwargs["model_tier"] == ModelTier.AUXILIARY

        async def test_generate_reaction_truncate_long(self, agent, mock_llm_router):
            """测试超长反应被截断"""
            long_reaction = "我真的很开心" * 30  # 超过150字
            mock_llm_router.generate.return_value = long_reaction

            result = await agent.generate_reaction(
                event="测试事件",
                character_name="角色",
                character_description="描述",
            )

            assert len(result) <= 150
            assert result.endswith("...")

        async def test_generate_reaction_fallback_on_exception(self, agent, mock_llm_router):
            """测试异常时返回默认兜底文本"""
            mock_llm_router.generate.side_effect = Exception("LLM 错误")

            result = await agent.generate_reaction(
                event="测试事件",
                character_name="小明",
                character_description="描述",
            )

            assert "小明" in result
            assert "默默地想着" in result

        async def test_generate_reaction_prompt_contains_character_info(self, agent, mock_llm_router):
            """测试 prompt 中包含角色信息"""
            mock_llm_router.generate.return_value = "反应内容"

            await agent.generate_reaction(
                event="发生了某事",
                character_name="特定角色名",
                character_description="特定的角色描述",
            )

            call_args = mock_llm_router.generate.call_args.kwargs
            messages = call_args["messages"]
            system_prompt = messages[0]["content"]
            assert "特定角色名" in system_prompt
            assert "特定的角色描述" in system_prompt
            assert "发生了某事" in messages[1]["content"]

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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
