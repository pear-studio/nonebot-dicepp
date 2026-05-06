"""ChatSession 行为单元测试

覆盖 chat() 入口前置逻辑（不进入 coordinator 路径）：

1. 5 秒去重：相同消息 5 秒内重复返回 None
2. 首次对话：私聊首次且角色配置了 first_mes，直接返回 first_mes，不调 LLM
3. 厌倦拒绝：relationship_refuse_enabled 且 warmth_level=0，按概率返回 refuse_messages
"""

import random
from datetime import datetime, timedelta

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
        timezone="",  # 解耦时区，与测试构造的 datetime.now() 对齐
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


class TestApplyTokenWindow:
    """_apply_token_window 行为测试"""

    def _make_gc(self, content, created_at, role="user", display_name="群友"):
        from plugins.DicePP.module.persona.data.models import GroupConversation
        return GroupConversation(
            user_id="u1",
            role=role,
            content=content,
            created_at=created_at,
            group_id="g1",
            display_name=display_name,
        )

    def test_empty_history(self):
        session = _make_session()
        result, truncated = session._apply_token_window([])
        assert result == []
        assert truncated is False

    def test_token_budget_limits_messages(self):
        """总 token 超过 budget 时停止收集（已收集的非空时）"""
        session = _make_session()
        session.config.group_context_budget_tokens = 10
        session.config.group_single_message_max_tokens = 100
        session.config.group_max_messages = 100
        session.config.group_max_age_minutes = 1000

        now = datetime.now()
        history = [
            self._make_gc("短消息1", now),
            self._make_gc("短消息2", now),
            self._make_gc("短消息3", now),
        ]
        result, truncated = session._apply_token_window(history)
        assert len(result) >= 1
        assert len(result) < len(history)
        assert truncated is True

    def test_long_message_truncated_to_single_max(self):
        """单条消息超过 single_max 会被截断"""
        session = _make_session()
        session.config.group_context_budget_tokens = 10000
        session.config.group_single_message_max_tokens = 5
        session.config.group_max_messages = 100
        session.config.group_max_age_minutes = 1000

        now = datetime.now()
        long_msg = "这是一个非常长的消息内容用于测试截断"
        history = [self._make_gc(long_msg, now)]
        result, truncated = session._apply_token_window(history)
        assert len(result) == 1
        assert len(result[0]["content"]) < len(long_msg)

    def test_time_window_filters_old_messages(self):
        """超过 max_age 的消息被过滤（reversed 遍历，遇到超时就 break）"""
        session = _make_session()
        session.config.group_context_budget_tokens = 10000
        session.config.group_single_message_max_tokens = 100
        session.config.group_max_messages = 100
        session.config.group_max_age_minutes = 10

        now = datetime.now()
        # 列表按时间升序排列（老在前），reversed 后从新到旧遍历
        history = [
            self._make_gc("老消息", now - timedelta(minutes=20)),
            self._make_gc("新消息", now),
        ]
        result, truncated = session._apply_token_window(history)
        assert len(result) == 1
        assert result[0]["content"] == "新消息"

    def test_speaker_name_injected(self):
        """speaker_name 根据 role 和 display_name 正确注入"""
        session = _make_session()
        session.config.group_context_budget_tokens = 10000
        session.config.group_single_message_max_tokens = 100
        session.config.group_max_messages = 100
        session.config.group_max_age_minutes = 1000

        now = datetime.now()
        history = [
            self._make_gc("用户消息", now, role="user", display_name="小明"),
            self._make_gc("机器人回复", now, role="assistant", display_name=""),
        ]
        result, truncated = session._apply_token_window(history)
        assert len(result) == 2
        assert result[0]["speaker_name"] == "小明"
        assert result[1]["speaker_name"] == "我"

    def test_keeps_at_least_one(self):
        """即使 budget 极小，也保底保留 1 条"""
        session = _make_session()
        session.config.group_context_budget_tokens = 1
        session.config.group_single_message_max_tokens = 100
        session.config.group_max_messages = 100
        session.config.group_max_age_minutes = 1000

        now = datetime.now()
        history = [
            self._make_gc("消息1", now),
            self._make_gc("消息2", now),
        ]
        result, truncated = session._apply_token_window(history)
        # 第一条因 result 为空而不被 budget break，保底保留 1 条
        assert len(result) == 1
        assert truncated is True


class TestDecayInChatSession:
    """_update_interaction 中的惰性 decay 路径测试"""

    @pytest.mark.asyncio
    async def test_update_interaction_applies_decay_when_due(self):
        """decay_calculator.should_apply_decay=True 时惰性应用衰减并写入 score_event"""
        from plugins.DicePP.module.persona.data.models import RelationshipState, ScoreDeltas

        session = _make_session()

        decay_calc = MagicMock()
        decay_calc.should_apply_decay = MagicMock(return_value=True)
        decay_calc.calculate_decay = MagicMock(return_value=(
            ScoreDeltas(intimacy=-3.0),
            "超过7天未互动",
        ))
        session.decay_calculator = decay_calc

        rel = RelationshipState(user_id="u1", group_id="")
        session.store.get_relationship = AsyncMock(return_value=rel)
        session.store.update_relationship = AsyncMock()
        session.store.add_score_event = AsyncMock()
        session.store.init_relationship = AsyncMock(return_value=rel)

        await session._update_interaction("u1", "", "user_msg", "assistant_msg")

        decay_calc.should_apply_decay.assert_called_once()
        decay_calc.calculate_decay.assert_called_once()
        session.store.add_score_event.assert_awaited_once()
        # 验证写入的 score_event 包含 decay 原因
        event = session.store.add_score_event.call_args[0][0]
        assert "time_decay" in event.reason
