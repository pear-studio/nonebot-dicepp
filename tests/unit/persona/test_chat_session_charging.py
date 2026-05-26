"""ChatSession 计费路径测试

确保"一次 LLM 调用 = 一次 increment_usage"不变量在以下场景成立：

1. 单次成功（无 buffered）：1 次 LLM 调用 → 1 次扣费（来自最终轮 _chat_via_coordinator）
2. 多轮合并（中间轮 + 最终轮）：N 次 LLM 调用 → N 次扣费
   （N-1 次来自 on_result，1 次来自最终轮）
3. 全部失败：调用 on_exhausted 路径 → 0 次扣费（兜底文案不计入 LLM 用量）
"""

import asyncio

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from plugins.DicePP.module.persona.chat.session import ChatSession, ChatConfig
from plugins.DicePP.module.persona.llm.coordinator import LLMCallCoordinator


@pytest.fixture(autouse=True)
def mock_agent_runtime():
    """Mock AgentRuntime.run_chat — 模拟 UsageSink 计费行为"""
    from plugins.DicePP.module.persona.agent.runtime import AgentRuntime
    from plugins.DicePP.module.persona.agent.loop import AgentRunResult

    original = AgentRuntime.run_chat
    result = AgentRunResult(
        run_id="test", turn_id="test", status="completed",
        final_reason="direct_content", final_text="reply",
        delivery_performed=False,
    )

    async def fake_run_chat(self, messages, user_id, group_id, **kwargs):
        # 模拟 UsageSink 在 AgentLoop 首次 LLM 调用后的计费
        await self._router.increment_usage(user_id)
        return result

    AgentRuntime.run_chat = fake_run_chat
    yield
    AgentRuntime.run_chat = original


def _make_session(coordinator: LLMCallCoordinator) -> ChatSession:
    """构造最小可运行 ChatSession，仅用于走 coordinator + charging 路径"""
    store = AsyncMock()
    store.get_recent_messages = AsyncMock(return_value=[])
    store.get_group_messages = AsyncMock(return_value=[])
    store.add_message_stream = AsyncMock(return_value=1)
    store._retain_message_stream = AsyncMock()
    store.add_score_event = AsyncMock()
    store.update_relationship = AsyncMock()
    store.init_relationship = AsyncMock()
    store.get_relationship = AsyncMock(return_value=None)
    store.get_user_profile = AsyncMock(return_value=None)
    store.save_user_profile = AsyncMock()

    router = MagicMock()
    router.increment_usage = AsyncMock()
    router.data_store = None
    router.quota_check_enabled = False
    router.daily_limit = 20
    router.trace_enabled = False
    router.trace_max_age_days = 7
    router.config = None

    character = MagicMock()
    character.name = "Test"
    character.extensions = MagicMock()
    character.extensions.initial_relationship = 30.0
    character.extensions.refuse_messages = None
    character.get_warmth_labels = MagicMock(return_value=["a", "b", "c", "d", "e", "f"])

    config = ChatConfig(
        relationship_refuse_enabled=False,
        scoring_interval=999,
    )

    scoring_trigger = MagicMock()
    scoring_trigger.effective_relationship = MagicMock(side_effect=lambda rel: rel)
    scoring_trigger.on_interaction = AsyncMock()
    scoring_trigger.update_character = MagicMock()

    response_handler = MagicMock()
    response_handler.port = None
    response_handler.persist = AsyncMock(return_value=1)
    response_handler.send = AsyncMock(return_value=True)
    response_handler.persist_and_send = AsyncMock(return_value=1)

    context_builder = MagicMock()
    context_builder.build = MagicMock(return_value=[{"role": "user", "content": "hi"}])
    context_builder.build_debug_info = MagicMock(return_value="")
    context_builder.format_history = MagicMock(side_effect=lambda h, is_group: h)
    context_builder.truncate_by_turns = MagicMock(side_effect=lambda h, *a, **kw: h)
    context_builder.build_lore_text = MagicMock(return_value={})

    session = ChatSession(
        store=store,
        router=router,
        tool_registry=MagicMock(),
        coordinator=coordinator,
        character=character,
        config=config,
        scoring_trigger=scoring_trigger,
        response_handler=response_handler,
        context_builder=context_builder,
    )
    return session


@pytest.mark.asyncio
async def test_single_call_charges_once():
    """单次成功调用 → 1 次扣费"""
    coordinator = LLMCallCoordinator()
    session = _make_session(coordinator)

    result = await session.chat("u1", "", "你好")

    assert result == "reply"
    session.router.increment_usage.assert_awaited_once_with("u1")


@pytest.mark.asyncio
async def test_buffered_merge_charges_per_call():
    """N 次 LLM 调用（中间轮 + 最终轮）→ N 次扣费（中间轮 on_result + 最终轮 success 各扣 1 次）"""
    coordinator = LLMCallCoordinator()
    session = _make_session(coordinator)

    llm_calls = []
    first_started = asyncio.Event()
    third_buffered = asyncio.Event()

    async def slow_chat_call(user_id, group_id, messages):
        llm_calls.append((user_id, group_id, messages))
        call_index = len(llm_calls)
        # 模拟 AgentRuntime 内部计费行为
        await session.router.increment_usage(user_id)
        if call_index == 1:
            first_started.set()
            await asyncio.sleep(0.05)
        elif call_index == 2:
            await asyncio.sleep(0.05)
        return f"reply_{call_index}"

    session._coordinator_chat_call_fn = slow_chat_call

    async def first():
        return await session.chat("u1", "", "msg1")

    async def second():
        await first_started.wait()
        return await session.chat("u1", "", "msg2")

    async def third():
        await first_started.wait()
        await asyncio.sleep(0.01)
        third_buffered.set()
        return await session.chat("u1", "", "msg3")

    await asyncio.gather(first(), second(), third())

    # 至少 2 次 LLM 调用（首轮 + 至少 1 次 buffered 合并）
    assert len(llm_calls) >= 2
    # 中间轮通过 on_result 各扣 1 次 + 最终轮 success 1 次：N 次 LLM 调用 → N 次扣费
    charged_user_ids = [
        call.args[0]
        for call in session.router.increment_usage.await_args_list
    ]
    assert charged_user_ids == ["u1"] * len(llm_calls)


@pytest.mark.asyncio
async def test_all_failures_does_not_charge():
    """全部失败走 on_exhausted → 0 次扣费"""
    coordinator = LLMCallCoordinator(max_failures=1, max_iterations=5)
    session = _make_session(coordinator)

    async def always_fail(user_id, group_id, messages):
        raise RuntimeError("LLM down")

    session._coordinator_chat_call_fn = always_fail

    result = await session.chat("u1", "", "msg")

    # 兜底文案
    assert "暂时不可用" in result
    session.router.increment_usage.assert_not_awaited()
