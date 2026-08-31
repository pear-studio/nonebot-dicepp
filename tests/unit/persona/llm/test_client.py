"""DeepSeekTextModelClient 的正常调用路径测试。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from plugins.DicePP.module.persona.llm.client import DeepSeekTextModelClient


def _fake_sdk():
    response = SimpleNamespace(
        choices=[SimpleNamespace(
            message=SimpleNamespace(
                content="ok",
                tool_calls=None,
                reasoning_content=None,
            ),
            finish_reason="stop",
        )],
        usage=None,
        model="fake-model",
    )
    create = AsyncMock(return_value=response)
    sdk = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=create),
        ),
    )
    return sdk, create


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("task", "timeout", "thinking_type"),
    [
        ("chat", 30, "enabled"),
        ("background", 90, "disabled"),
        ("action_evaluation", 30, "disabled"),
    ],
)
async def test_generate_uses_internal_task_request_profile(
    task, timeout, thinking_type,
):
    client = DeepSeekTextModelClient(
        api_key="sk-test",
        model="deepseek-v4-flash",
        base_url="https://api.deepseek.com",
    )
    sdk, create = _fake_sdk()
    client._transport._client = sdk
    observed_timeout = {}

    async def wait_for(awaitable, timeout):
        observed_timeout["value"] = timeout
        return await awaitable

    with patch(
        "plugins.DicePP.module.persona.llm.providers.deepseek.asyncio.wait_for",
        new=wait_for,
    ):
        result = await client.generate(
            messages=[{"role": "user", "content": "hi"}],
            tools=[{"type": "function"}],
            task=task,
        )

    assert result.content == "ok"
    assert observed_timeout["value"] == timeout
    kwargs = create.call_args.kwargs
    assert kwargs == {
        "model": "deepseek-v4-flash",
        "messages": [{"role": "user", "content": "hi"}],
        "tools": [{"type": "function"}],
        "tool_choice": "auto",
        "extra_body": {"thinking": {"type": thinking_type}},
    }


def test_unknown_tasks_use_chat_profile():
    assert DeepSeekTextModelClient._profile_for("chat").timeout == 30
    assert DeepSeekTextModelClient._profile_for("chat").thinking is True
    assert DeepSeekTextModelClient._profile_for("unknown").timeout == 30
    assert DeepSeekTextModelClient._profile_for("unknown").thinking is True
    assert DeepSeekTextModelClient._profile_for("summary").timeout == 90
    assert DeepSeekTextModelClient._profile_for("summary").thinking is False
    assert DeepSeekTextModelClient._profile_for("action_evaluation").timeout == 30
    assert DeepSeekTextModelClient._profile_for("action_evaluation").thinking is False
