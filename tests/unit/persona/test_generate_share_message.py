"""
单元测试: generate_share_message

覆盖 CollectExecutor 收集、截断逻辑、few-shot 注入。
"""

import pytest
from unittest.mock import MagicMock, AsyncMock

from plugins.DicePP.module.persona.life.event_agent import (
    EventGenerationAgent,
    ShareMessageContext,
)
from plugins.DicePP.module.persona.data.models import ModelTier


class MockConfig:
    proactive_share_max_chars = 200
    background_llm_timeout_seconds = 10
    background_llm_max_tool_rounds = 3


def _make_side_effect(args_json: str, tool_name: str = "record_share_message"):
    """创建 router.generate 的 side_effect，调用 tool_executor 填充 CollectExecutor"""
    async def side_effect(**kwargs):
        tool_executor = kwargs.get("tool_executor")
        if tool_executor:
            tc = {
                "id": "tc_1",
                "name": tool_name,
                "arguments": args_json,
            }
            await tool_executor([tc])
        return "", {}
    return side_effect


@pytest.fixture
def mock_llm_router():
    router = MagicMock()
    router.generate = AsyncMock()
    return router


@pytest.fixture
def agent(mock_llm_router):
    return EventGenerationAgent(mock_llm_router, config=MockConfig())


@pytest.fixture
def base_context():
    return ShareMessageContext(
        event_description="在公园长椅上打盹，被鸽子踩醒了",
        reaction="吓了一跳，然后笑了",
        character_name="七七",
        character_description="一个喜欢户外活动的女孩",
        target_user_id="u1",
        relationship_score=65.0,
        warmth_label="友好",
        user_profile_facts="- 昵称：小明\n- 爱好：摄影",
        recent_history="- 用户: 今天天气不错\n- 我: 是啊，适合出去走走",
        message_type="random_event",
        environment="private",
    )


@pytest.mark.asyncio
async def test_generate_share_message_success(agent, mock_llm_router, base_context):
    """正常返回分享消息"""
    mock_llm_router.generate.side_effect = _make_side_effect(
        '{"message": "刚才在公园长椅上眯了一会儿，被鸽子踩醒了"}',
    )

    result = await agent.generate_share_message(base_context)

    assert result == "刚才在公园长椅上眯了一会儿，被鸽子踩醒了"
    mock_llm_router.generate.assert_called_once()
    call_kwargs = mock_llm_router.generate.call_args.kwargs
    assert call_kwargs["model_tier"] == ModelTier.AUXILIARY
    assert call_kwargs["temperature"] == 0.85
    assert "tools" in call_kwargs
    assert call_kwargs["max_tool_rounds"] == 3


@pytest.mark.asyncio
async def test_generate_share_message_strip_quotes(agent, mock_llm_router, base_context):
    """去除消息中的引号"""
    mock_llm_router.generate.side_effect = _make_side_effect(
        '{"message": "\\"带引号的消息\\""}',
    )

    result = await agent.generate_share_message(base_context)

    assert result == "带引号的消息"


@pytest.mark.asyncio
async def test_generate_share_message_truncate_long(agent, mock_llm_router, base_context):
    """超长消息被截断到 config.max_chars"""
    long_msg = "哈" * 300
    mock_llm_router.generate.side_effect = _make_side_effect(
        f'{{"message": "{long_msg}"}}',
    )

    result = await agent.generate_share_message(base_context)

    assert len(result) <= 200
    assert result.endswith("...")


@pytest.mark.asyncio
async def test_generate_share_message_llm_error_returns_none(agent, mock_llm_router, base_context):
    """LLM 错误时返回 None"""
    mock_llm_router.generate.side_effect = Exception("LLM 错误")

    result = await agent.generate_share_message(base_context)

    assert result is None


@pytest.mark.asyncio
async def test_generate_share_message_empty_message(agent, mock_llm_router, base_context):
    """LLM 返回空消息时返回 None"""
    mock_llm_router.generate.side_effect = _make_side_effect(
        '{"message": ""}',
    )

    result = await agent.generate_share_message(base_context)

    assert result is None


@pytest.mark.asyncio
async def test_generate_share_message_no_collected(agent, mock_llm_router, base_context):
    """LLM 未调用工具时返回 None"""
    # side_effect 不调用 tool_executor → executor.collected 为空
    async def no_tool_call(**kwargs):
        return "text without tool", {}

    mock_llm_router.generate.side_effect = no_tool_call

    result = await agent.generate_share_message(base_context)

    assert result is None


@pytest.mark.asyncio
async def test_generate_share_message_few_shot_default(agent, mock_llm_router, base_context):
    """默认 few-shot 示例被注入 prompt"""
    mock_llm_router.generate.side_effect = _make_side_effect(
        '{"message": "测试消息"}',
    )

    await agent.generate_share_message(base_context)

    call_kwargs = mock_llm_router.generate.call_args.kwargs
    messages = call_kwargs["messages"]
    system_prompt = messages[0]["content"]
    assert "示例:" in system_prompt
    assert "鸽子" in system_prompt
    assert "七七" in system_prompt
    assert "{{character_name}}" not in system_prompt


@pytest.mark.asyncio
async def test_generate_share_message_few_shot_empty_list(agent, mock_llm_router, base_context):
    """空列表时不注入 few-shot"""
    base_context.share_message_examples = []
    mock_llm_router.generate.side_effect = _make_side_effect(
        '{"message": "测试消息"}',
    )

    await agent.generate_share_message(base_context)

    call_kwargs = mock_llm_router.generate.call_args.kwargs
    messages = call_kwargs["messages"]
    system_prompt = messages[0]["content"]
    assert "示例:" not in system_prompt


@pytest.mark.asyncio
async def test_generate_share_message_few_shot_custom(agent, mock_llm_router, base_context):
    """自定义 few-shot 示例"""
    base_context.share_message_examples = [
        "场景：下雨了\n消息：\"下雨了，记得带伞\"\n→ 好示例"
    ]
    mock_llm_router.generate.side_effect = _make_side_effect(
        '{"message": "测试消息"}',
    )

    await agent.generate_share_message(base_context)

    call_kwargs = mock_llm_router.generate.call_args.kwargs
    messages = call_kwargs["messages"]
    system_prompt = messages[0]["content"]
    assert "下雨了" in system_prompt
    assert "七七" in system_prompt


@pytest.mark.asyncio
async def test_generate_share_message_no_config_fallback(agent, mock_llm_router, base_context):
    """未传入 config 时使用默认值"""
    agent_no_config = EventGenerationAgent(mock_llm_router)
    mock_llm_router.generate.side_effect = _make_side_effect(
        '{"message": "默认行为"}',
    )

    result = await agent_no_config.generate_share_message(base_context)

    assert result == "默认行为"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
