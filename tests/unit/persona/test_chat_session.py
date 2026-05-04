"""ChatSession 行为单元测试

覆盖 chat() 入口前置逻辑（不进入 coordinator 路径）：

1. 5 秒去重：相同消息 5 秒内重复返回 None
2. 首次对话：私聊首次且角色配置了 first_mes，直接返回 first_mes，不调 LLM
3. 厌倦拒绝：relationship_refuse_enabled 且 warmth_level=0，按概率返回 refuse_messages
"""

import random

import pytest
from unittest.mock import AsyncMock, MagicMock

from plugins.DicePP.module.persona.chat.session import ChatSession, ChatConfig
from plugins.DicePP.module.persona.data.models import RelationshipState
from plugins.DicePP.module.persona.llm.coordinator import LLMCallCoordinator


def _make_session(
    *,
    refuse_enabled: bool = False,
    first_mes: str = "",
    relationship: RelationshipState = None,
    refuse_messages=None,
) -> ChatSession:
    """构造最小可运行 ChatSession（mock 全部依赖）"""
    store = AsyncMock()
    store.get_recent_messages = AsyncMock(return_value=[])
    store.get_group_conversations = AsyncMock(return_value=[])
    store.add_message = AsyncMock()
    store.add_group_conversation = AsyncMock()
    store.add_score_event = AsyncMock()
    store.update_relationship = AsyncMock()
    store.init_relationship = AsyncMock()
    store.get_relationship = AsyncMock(return_value=relationship)
    store.get_user_profile = AsyncMock(return_value=None)
    store.save_user_profile = AsyncMock()
    store.prune_old_messages = AsyncMock()

    router = MagicMock()
    router.increment_usage = AsyncMock()
    router.generate = AsyncMock(return_value="reply")
    router.generate_with_tools = AsyncMock(return_value=("reply", {}))

    character = MagicMock()
    character.name = "Test"
    character.first_mes = first_mes
    character.extensions = MagicMock()
    character.extensions.initial_relationship = 30.0
    character.extensions.refuse_messages = refuse_messages
    # 默认全部冰冷（warmth_level=0），方便触发拒绝
    character.get_warmth_labels = MagicMock(
        return_value=["冰冷", "陌生", "熟识", "亲密", "深爱", "灵魂"]
    )

    config = ChatConfig(
        tools_enabled=False,
        relationship_refuse_enabled=refuse_enabled,
        relationship_refuse_prob_base=0.3,
        relationship_refuse_prob_max=0.9,
        scoring_interval=999,
    )

    context_builder = MagicMock()
    context_builder.build = MagicMock(return_value=[{"role": "user", "content": "hi"}])
    context_builder.build_debug_info = MagicMock(return_value="")
    context_builder.truncate_by_turns = MagicMock(side_effect=lambda h, _: h)
    context_builder.build_lore_text = MagicMock(return_value={})

    return ChatSession(
        store=store,
        router=router,
        tool_registry=MagicMock(),
        coordinator=LLMCallCoordinator(),
        character=character,
        config=config,
        scoring_agent=MagicMock(),
        context_builder=context_builder,
    )


@pytest.mark.asyncio
async def test_dedup_within_5_seconds_returns_none():
    """5 秒内重复消息直接返回 None，不进入 LLM 路径"""
    session = _make_session()

    first = await session.chat("u1", "", "你好")
    assert first is not None

    # 立刻发送相同消息：应被去重
    again = await session.chat("u1", "", "你好")
    assert again is None
    # 第二次不应触发任何 router 调用
    assert session.router.generate.await_count <= 1


@pytest.mark.asyncio
async def test_dedup_different_message_not_skipped():
    """5 秒内不同消息正常处理"""
    session = _make_session()

    a = await session.chat("u1", "", "你好")
    b = await session.chat("u1", "", "再见")

    assert a is not None
    assert b is not None


@pytest.mark.asyncio
async def test_first_message_uses_first_mes_without_llm():
    """私聊首条消息且角色配置了 first_mes，直接返回 first_mes"""
    session = _make_session(first_mes="你好，我是测试角色")

    result = await session.chat("u1", "", "Hi")

    assert result == "你好，我是测试角色"
    # 不应调用 LLM
    assert session.router.generate.await_count == 0
    assert session.router.generate_with_tools.await_count == 0
    # 应该已写入用户消息和 first_mes 两条
    assert session.store.add_message.await_count >= 2


@pytest.mark.asyncio
async def test_first_message_skipped_in_group_chat():
    """群聊不走 first_mes 分支，正常进入 LLM 路径"""
    session = _make_session(first_mes="你好，我是测试角色")

    # 群聊场景应走正常流程（不会触发 first_mes 提前返回）
    result = await session.chat("u1", "g1", "Hi")
    assert result != "你好，我是测试角色"


@pytest.mark.asyncio
async def test_refuse_triggers_at_warmth_zero(monkeypatch):
    """warmth_level=0 时按概率返回 refuse 文案，跳过 LLM"""
    rel = RelationshipState(
        user_id="u1", group_id="",
        intimacy=0, passion=0, trust=0, secureness=0,
    )
    session = _make_session(
        refuse_enabled=True,
        relationship=rel,
        refuse_messages=["...（已读不回）"],
    )

    # 历史已有消息，绕开 is_first 分支
    session.store.get_recent_messages = AsyncMock(return_value=[
        MagicMock(role="user", content="prev"),
    ])

    # 概率始终 < p_refuse
    monkeypatch.setattr(random, "random", lambda: 0.0)

    result = await session.chat("u1", "", "你好")

    assert result == "...（已读不回）"
    assert session.router.generate.await_count == 0


@pytest.mark.asyncio
async def test_refuse_does_not_trigger_above_warmth_zero(monkeypatch):
    """warmth_level>0 时不进入拒绝分支"""
    # 高分 → warmth_level>0
    rel = RelationshipState(
        user_id="u1", group_id="",
        intimacy=80, passion=80, trust=80, secureness=80,
    )
    session = _make_session(
        refuse_enabled=True,
        relationship=rel,
        refuse_messages=["...（已读不回）"],
    )

    session.store.get_recent_messages = AsyncMock(return_value=[
        MagicMock(role="user", content="prev"),
    ])

    monkeypatch.setattr(random, "random", lambda: 0.0)

    result = await session.chat("u1", "", "你好")

    assert result != "...（已读不回）"
