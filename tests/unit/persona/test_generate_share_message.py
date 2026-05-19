"""
单元测试: generate_share_message（使用 router.run_via_loop）
"""
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from plugins.DicePP.module.persona.life.event_agent import (
    EventGenerationAgent, ShareMessageContext,
)
from plugins.DicePP.module.persona.llm.providers.protocol import LLMResponse, TokenUsage, ToolCall
from conftest import attach_mock_run_via_loop


def _resp(content="", tool_calls=None):
    return LLMResponse(content=content, tool_calls=tool_calls or [],
                       usage=TokenUsage(), finish_reason="tool_calls" if tool_calls else "stop", model="gpt-4o")


class MockConfig:
    proactive_share_max_chars = 200
    background_llm_timeout_seconds = 10
    background_llm_max_tool_rounds = 3


@pytest.fixture
def mock_router():
    router = MagicMock()
    router.data_store = None
    router.quota_check_enabled = False
    router.daily_limit = 20
    router.trace_enabled = False
    router.trace_max_age_days = 7
    router.config = None
    router._pending_tool_args = None
    attach_mock_run_via_loop(router)
    return router


@pytest.fixture
def agent(mock_router):
    return EventGenerationAgent(mock_router, config=MockConfig())


@pytest.fixture
def base_context():
    return ShareMessageContext(
        event_description="在公园长椅上打盹，被鸽子踩醒了",
        reaction="吓了一跳，然后笑了",
        character_name="七七", character_description="一个喜欢户外活动的女孩",
        target_user_id="u1", relationship_score=65.0,
        warmth_label="友好", user_profile_facts="- 昵称：小明\n- 爱好：摄影",
        recent_history="- 用户: 今天天气不错\n- 我: 是啊，适合出去走走",
        message_type="random_event", environment="private",
    )


@pytest.mark.asyncio
async def test_generate_share_message_success(agent, mock_router, base_context):
    mock_router._pending_tool_args = '{"message": "刚才在公园长椅上眯了一会儿，被鸽子踩醒了"}'

    result = await agent.generate_share_message(base_context)
    assert result == "刚才在公园长椅上眯了一会儿，被鸽子踩醒了"


@pytest.mark.asyncio
async def test_generate_share_message_strip_quotes(agent, mock_router, base_context):
    mock_router._pending_tool_args = '{"message": "\\"带引号的消息\\""}'

    result = await agent.generate_share_message(base_context)
    assert result == "带引号的消息"


@pytest.mark.asyncio
async def test_generate_share_message_truncate_long(agent, mock_router, base_context):
    long_msg = "哈" * 300
    mock_router._pending_tool_args = f'{{"message": "{long_msg}"}}'

    result = await agent.generate_share_message(base_context)
    assert len(result) <= 200
    assert result.endswith("...")


@pytest.mark.asyncio
async def test_generate_share_message_llm_error_returns_none(agent, mock_router, base_context):
    mock_router.run_via_loop.side_effect = Exception("LLM 错误")
    result = await agent.generate_share_message(base_context)
    assert result is None


@pytest.mark.asyncio
async def test_generate_share_message_empty_message(agent, mock_router, base_context):
    mock_router._pending_tool_args = '{"message": ""}'

    with patch("asyncio.sleep", new_callable=AsyncMock):
        result = await agent.generate_share_message(base_context)
    assert result is None


@pytest.mark.asyncio
async def test_generate_share_message_no_collected(agent, mock_router, base_context):
    mock_router._pending_tool_args = None
    result = await agent.generate_share_message(base_context)
    assert result is None


@pytest.mark.asyncio
async def test_generate_share_message_few_shot_default(agent, mock_router, base_context):
    mock_router._pending_tool_args = '{"message": "测试消息"}'

    await agent.generate_share_message(base_context)
    assert mock_router.run_via_loop.called
    call_kwargs = mock_router.run_via_loop.call_args.kwargs
    msgs = call_kwargs["messages"]
    system_prompt = msgs[0]["content"]
    assert "示例:" in system_prompt
    assert "鸽子" in system_prompt
    assert "{{character_name}}" not in system_prompt


@pytest.mark.asyncio
async def test_generate_share_message_few_shot_empty_list(agent, mock_router, base_context):
    base_context.share_message_examples = []
    mock_router._pending_tool_args = '{"message": "测试消息"}'

    await agent.generate_share_message(base_context)
    call_kwargs = mock_router.run_via_loop.call_args.kwargs
    system_prompt = call_kwargs["messages"][0]["content"]
    assert "示例:" not in system_prompt


@pytest.mark.asyncio
async def test_generate_share_message_few_shot_custom(agent, mock_router, base_context):
    base_context.share_message_examples = ["场景：下雨了\n消息：\"下雨了，记得带伞\"\n→ 好示例"]
    mock_router._pending_tool_args = '{"message": "测试消息"}'

    await agent.generate_share_message(base_context)
    system_prompt = mock_router.run_via_loop.call_args.kwargs["messages"][0]["content"]
    assert "下雨了" in system_prompt


@pytest.mark.asyncio
async def test_generate_share_message_no_config_fallback(agent, mock_router, base_context):
    agent_no_config = EventGenerationAgent(mock_router)
    mock_router._pending_tool_args = '{"message": "默认行为"}'

    result = await agent_no_config.generate_share_message(base_context)
    assert result == "默认行为"
