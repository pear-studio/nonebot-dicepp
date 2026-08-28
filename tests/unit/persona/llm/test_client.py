"""DeepSeekTextModelClient 的正常调用路径测试。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from plugins.DicePP.module.persona.llm.client import DeepSeekTextModelClient
from plugins.DicePP.module.persona.llm.providers.protocol import LLMResponse


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("task", "timeout", "thinking"),
    [
        ("chat", 30, True),
        ("background", 90, False),
        ("action_evaluation", 30, False),
    ],
)
async def test_generate_uses_internal_task_request_profile(task, timeout, thinking):
    response = LLMResponse(content="ok")
    provider = SimpleNamespace(generate=AsyncMock(return_value=response))

    with patch(
        "plugins.DicePP.module.persona.llm.client.OpenAIProvider",
        return_value=provider,
    ):
        client = DeepSeekTextModelClient(
            api_key="sk-test",
            model="deepseek-v4-flash",
            base_url="https://api.deepseek.com",
        )
        result = await client.generate(
            messages=[{"role": "user", "content": "hi"}],
            tools=[{"type": "function"}],
            task=task,
        )

    assert result is response
    provider.generate.assert_awaited_once_with(
        messages=[{"role": "user", "content": "hi"}],
        tools=[{"type": "function"}],
        temperature=None,
        timeout=timeout,
        tool_choice="auto",
        thinking=thinking,
    )


def test_unknown_tasks_use_chat_profile():
    assert DeepSeekTextModelClient._profile_for("chat").timeout == 30
    assert DeepSeekTextModelClient._profile_for("chat").thinking is True
    assert DeepSeekTextModelClient._profile_for("unknown").timeout == 30
    assert DeepSeekTextModelClient._profile_for("unknown").thinking is True
    assert DeepSeekTextModelClient._profile_for("summary").timeout == 90
    assert DeepSeekTextModelClient._profile_for("summary").thinking is False
    assert DeepSeekTextModelClient._profile_for("action_evaluation").timeout == 30
    assert DeepSeekTextModelClient._profile_for("action_evaluation").thinking is False
