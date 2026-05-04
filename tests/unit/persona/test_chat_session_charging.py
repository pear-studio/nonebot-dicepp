"""ChatSession 计费路径测试

确保"一次 LLM 调用 = 一次 increment_usage"不变量在以下场景成立：

1. 单次成功（无 buffered）：1 次 LLM 调用 → 1 次扣费（来自最终轮 _chat_via_coordinator）
2. 多轮合并（中间轮 + 最终轮）：N 次 LLM 调用 → N 次扣费
   （N-1 次来自 on_result，1 次来自最终轮）
3. 全部失败：调用 on_exhausted 路径 → 0 次扣费（兜底文案不计入 LLM 用量）
"""

import asyncio

import pytest
from unittest.mock import AsyncMock, MagicMock

from plugins.DicePP.module.persona.chat.session import ChatSession, ChatConfig
from plugins.DicePP.module.persona.llm.coordinator import LLMCallCoordinator


def _make_session(coordinator: LLMCallCoordinator) -> ChatSession:
    """构造最小可运行 ChatSession，仅用于走 coordinator + charging 路径"""
    store = AsyncMock()
    store.get_recent_messages = AsyncMock(return_value=[])
    store.get_group_conversations = AsyncMock(return_value=[])
    store.add_message = AsyncMock()
    store.add_group_conversation = AsyncMock()
    store.add_score_event = AsyncMock()
    store.update_relationship = AsyncMock()
    store.init_relationship = AsyncMock()
    store.get_relationship = AsyncMock(return_value=None)
    store.get_user_profile = AsyncMock(return_value=None)
    store.save_user_profile = AsyncMock()
    store.prune_old_messages = AsyncMock()

    router = MagicMock()
    router.increment_usage = AsyncMock()
    router.generate = AsyncMock(return_value="reply")
    router.generate_with_tools = AsyncMock(return_value=("reply", {}))

    character = MagicMock()
    character.name = "Test"
    character.first_mes = ""
    character.extensions = MagicMock()
    character.extensions.initial_relationship = 30.0
    character.extensions.refuse_messages = None
    character.get_warmth_labels = MagicMock(return_value=["a", "b", "c", "d", "e", "f"])

    config = ChatConfig(
        tools_enabled=False,
        relationship_refuse_enabled=False,
        scoring_interval=999,
    )

    scoring_agent = MagicMock()
    context_builder = MagicMock()
    context_builder.build = MagicMock(return_value=[{"role": "user", "content": "hi"}])
    context_builder.build_debug_info = MagicMock(return_value="")
    context_builder.truncate_by_turns = MagicMock(side_effect=lambda h, _: h)
    context_builder.build_lore_text = MagicMock(return_value={})

    session = ChatSession(
        store=store,
        router=router,
        tool_registry=MagicMock(),
        coordinator=coordinator,
        character=character,
        config=config,
        scoring_agent=scoring_agent,
        context_builder=context_builder,
    )
    return session


@pytest.mark.asyncio
async def test_single_call_charges_once():
    """单次成功调用 → 1 次扣费"""
    coordinator = LLMCallCoordinator()
    session = _make_session(coordinator)

    result = await session.chat("u1", "", "你好")

    assert result is not None
    assert session.router.increment_usage.await_count == 1


@pytest.mark.asyncio
async def test_buffered_merge_charges_per_call():
    """3 次 LLM 调用（2 中间 + 1 最终）→ 3 次扣费"""
    coordinator = LLMCallCoordinator()
    session = _make_session(coordinator)

    call_count = 0
    first_started = asyncio.Event()
    third_buffered = asyncio.Event()

    async def slow_chat_call(user_id, group_id, messages):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            first_started.set()
            await asyncio.sleep(0.05)  # 留出 buffered 注入窗口
        elif call_count == 2:
            await asyncio.sleep(0.05)
        return f"reply_{call_count}"

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

    # call_count 至少 2（首轮 + 至少 1 次 buffered 合并）
    assert call_count >= 2
    # 计费次数等于实际 LLM 调用次数
    assert session.router.increment_usage.await_count == call_count


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
    assert result is not None
    assert "暂时不可用" in result
    assert session.router.increment_usage.await_count == 0
