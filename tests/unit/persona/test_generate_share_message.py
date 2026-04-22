"""
单元测试: generate_share_message

覆盖重试逻辑、超时处理、截断逻辑、few-shot 注入。
"""

import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock

from plugins.DicePP.module.persona.agents.event_agent import (
    EventGenerationAgent,
    ShareMessageContext,
)
from plugins.DicePP.module.persona.data.models import ModelTier


class MockConfig:
    proactive_share_max_chars = 200
    proactive_share_max_retries = 2
    proactive_share_timeout_seconds = 10
    proactive_share_backoff_base_seconds = 2


@pytest.fixture
def mock_llm_router():
    router = MagicMock()
    router.generate_with_forced_tool = AsyncMock()
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
    mock_llm_router.generate_with_forced_tool.return_value = (
        '{"message": "刚才在公园长椅上眯了一会儿，被鸽子踩醒了"}',
        {},
    )

    result = await agent.generate_share_message(base_context)

    assert result == "刚才在公园长椅上眯了一会儿，被鸽子踩醒了"
    mock_llm_router.generate_with_forced_tool.assert_called_once()
    call_kwargs = mock_llm_router.generate_with_forced_tool.call_args.kwargs
    assert call_kwargs["model_tier"] == ModelTier.AUXILIARY
    assert call_kwargs["temperature"] == 0.85
    assert "record_share_message" in call_kwargs["tool_name"]


@pytest.mark.asyncio
async def test_generate_share_message_strip_quotes(agent, mock_llm_router, base_context):
    """去除消息中的引号"""
    mock_llm_router.generate_with_forced_tool.return_value = (
        '{"message": "\\"带引号的消息\\""}',
        {},
    )

    result = await agent.generate_share_message(base_context)

    assert result == "带引号的消息"


@pytest.mark.asyncio
async def test_generate_share_message_truncate_long(agent, mock_llm_router, base_context):
    """超长消息被截断到 config.max_chars"""
    long_msg = "哈" * 300
    mock_llm_router.generate_with_forced_tool.return_value = (
        f'{{"message": "{long_msg}"}}',
        {},
    )

    result = await agent.generate_share_message(base_context)

    assert len(result) <= 200
    assert result.endswith("...")


@pytest.mark.asyncio
async def test_generate_share_message_timeout_raises(agent, mock_llm_router, base_context):
    """超时错误直接抛出，由 LLMClient 内层处理，event_agent 层不重试"""
    mock_llm_router.generate_with_forced_tool.side_effect = asyncio.TimeoutError

    with pytest.raises(asyncio.TimeoutError):
        await agent.generate_share_message(base_context)

    assert mock_llm_router.generate_with_forced_tool.call_count == 1


@pytest.mark.asyncio
async def test_generate_share_message_llm_error_raises(agent, mock_llm_router, base_context):
    """LLM 网络/API 错误直接抛出，由 LLMClient 内层处理，event_agent 层不重试"""
    mock_llm_router.generate_with_forced_tool.side_effect = Exception("LLM 错误")

    with pytest.raises(Exception, match="LLM 错误"):
        await agent.generate_share_message(base_context)

    assert mock_llm_router.generate_with_forced_tool.call_count == 1


@pytest.mark.asyncio
async def test_generate_share_message_json_decode_retry(agent, mock_llm_router, base_context):
    """JSON 解析失败时 event_agent 层重试"""
    mock_llm_router.generate_with_forced_tool.side_effect = [
        ("invalid json", {}),
        ('{"message": "第二次成功了"}', {}),
    ]

    result = await agent.generate_share_message(base_context)

    assert result == "第二次成功了"
    assert mock_llm_router.generate_with_forced_tool.call_count == 2


@pytest.mark.asyncio
async def test_generate_share_message_empty_message(agent, mock_llm_router, base_context):
    """LLM 返回空消息时继续重试"""
    mock_llm_router.generate_with_forced_tool.return_value = (
        '{"message": ""}',
        {},
    )

    result = await agent.generate_share_message(base_context)

    assert result is None


@pytest.mark.asyncio
async def test_generate_share_message_few_shot_default(agent, mock_llm_router, base_context):
    """默认 few-shot 示例被注入 prompt"""
    mock_llm_router.generate_with_forced_tool.return_value = (
        '{"message": "测试消息"}',
        {},
    )

    await agent.generate_share_message(base_context)

    call_kwargs = mock_llm_router.generate_with_forced_tool.call_args.kwargs
    messages = call_kwargs["messages"]
    system_prompt = messages[0]["content"]
    assert "示例:" in system_prompt
    assert "鸽子" in system_prompt
    assert "七七" in system_prompt  # 角色名替换
    assert "{{character_name}}" not in system_prompt  # 占位符已被替换


@pytest.mark.asyncio
async def test_generate_share_message_few_shot_empty_list(agent, mock_llm_router, base_context):
    """空列表时不注入 few-shot"""
    base_context.share_message_examples = []
    mock_llm_router.generate_with_forced_tool.return_value = (
        '{"message": "测试消息"}',
        {},
    )

    await agent.generate_share_message(base_context)

    call_kwargs = mock_llm_router.generate_with_forced_tool.call_args.kwargs
    messages = call_kwargs["messages"]
    system_prompt = messages[0]["content"]
    assert "示例:" not in system_prompt


@pytest.mark.asyncio
async def test_generate_share_message_few_shot_custom(agent, mock_llm_router, base_context):
    """自定义 few-shot 示例"""
    base_context.share_message_examples = [
        "场景：下雨了\n消息：\"下雨了，记得带伞\"\n→ 好示例"
    ]
    mock_llm_router.generate_with_forced_tool.return_value = (
        '{"message": "测试消息"}',
        {},
    )

    await agent.generate_share_message(base_context)

    call_kwargs = mock_llm_router.generate_with_forced_tool.call_args.kwargs
    messages = call_kwargs["messages"]
    system_prompt = messages[0]["content"]
    assert "下雨了" in system_prompt
    assert "七七" in system_prompt


@pytest.mark.asyncio
async def test_generate_share_message_no_config_fallback(agent, mock_llm_router, base_context):
    """未传入 config 时使用默认值"""
    agent_no_config = EventGenerationAgent(mock_llm_router)
    mock_llm_router.generate_with_forced_tool.return_value = (
        '{"message": "默认行为"}',
        {},
    )

    result = await agent_no_config.generate_share_message(base_context)

    assert result == "默认行为"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
