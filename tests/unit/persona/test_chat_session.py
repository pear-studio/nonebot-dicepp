"""ChatSession 行为单元测试

覆盖 chat() 入口前置逻辑（不进入 coordinator 路径）：

1. 5 秒去重：相同消息 5 秒内重复返回 None
2. 厌倦拒绝：relationship_refuse_enabled 且 relation_level=0，按概率返回 refuse_messages
3. 分段回复集成：_chat_with_tools flag 生命周期、coordinator 协作、返回值语义
"""

import asyncio
import random
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from plugins.DicePP.utils.time import wall_now

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from plugins.DicePP.module.persona.chat.session import ChatSession, ChatConfig
from plugins.DicePP.module.persona.data.models import DailyEvent, RelationshipState
from plugins.DicePP.module.persona.data.store import PersonaDataStore
from plugins.DicePP.module.persona.llm.coordinator import LLMCallCoordinator
from plugins.DicePP.module.persona.character.models import Character
from plugins.DicePP.module.persona.chat.context import ContextBuilder


# ── fixtures: 非分段路径 ───────────────────────────────────────────────────


@pytest.fixture()
def mock_agent_runtime():
    """Mock AgentRuntime.run_chat — 避免依赖真实 LLM 调用"""
    from plugins.DicePP.module.persona.agent.runtime import AgentRuntime
    from plugins.DicePP.module.persona.agent.loop import AgentRunResult

    original = AgentRuntime.run_chat
    result = AgentRunResult(
        run_id="test", turn_id="test", status="completed",
        final_reason="direct_content", final_text="reply",
        delivery_performed=False,
    )
    calls: list = []

    async def fake_run_chat(self, messages, user_id, group_id, **kwargs):
        calls.append((messages, user_id, group_id))
        return result

    AgentRuntime.run_chat = fake_run_chat
    yield calls
    AgentRuntime.run_chat = original


def _make_session(
    *,
    refuse_enabled: bool = False,
    relationship: RelationshipState = None,
    refuse_messages=None,
    coordinator: LLMCallCoordinator = None,
) -> ChatSession:
    """构造最小可运行 ChatSession（mock 全部依赖）"""
    store = AsyncMock()
    store.get_recent_messages = AsyncMock(return_value=[])
    store.get_group_messages = AsyncMock(return_value=[])
    store.add_message_stream = AsyncMock(return_value=1)
    store._retain_message_stream = AsyncMock()
    store.add_score_event = AsyncMock()
    store.update_relationship = AsyncMock()
    store.init_relationship = AsyncMock()
    store.get_relationship = AsyncMock(return_value=relationship)
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
    character.extensions.refuse_messages = refuse_messages
    # 默认全部冰冷（relation_level=0），方便触发拒绝
    character.get_relation_labels = MagicMock(
        return_value=["冰冷", "陌生", "熟识", "亲密", "深爱", "灵魂"]
    )

    config = ChatConfig(
        relationship_refuse_enabled=refuse_enabled,
        scoring_interval=999,
        timezone="Asia/Shanghai",  # 与 wall_now() 默认时区一致
    )

    context_builder = MagicMock()
    context_builder.build = MagicMock(return_value=[{"role": "user", "content": "hi"}])
    context_builder.build_debug_info = MagicMock(return_value="")
    context_builder.format_history = MagicMock(side_effect=lambda h, is_group: h)
    context_builder.truncate_by_turns = MagicMock(side_effect=lambda h, *a, **kw: h)
    context_builder.build_lore_text = MagicMock(return_value={})

    scoring_trigger = MagicMock()
    scoring_trigger.effective_relationship = MagicMock(side_effect=lambda rel: rel)
    scoring_trigger.on_interaction = AsyncMock()
    scoring_trigger.update_character = MagicMock()

    response_handler = MagicMock()
    response_handler.port = None
    response_handler.persist = AsyncMock(return_value=1)
    response_handler.send = AsyncMock(return_value=True)
    response_handler.persist_and_send = AsyncMock(return_value=1)

    return ChatSession(
        store=store,
        router=router,
        tool_registry=MagicMock(),
        coordinator=coordinator if coordinator is not None else LLMCallCoordinator(),
        character=character,
        config=config,
        scoring_trigger=scoring_trigger,
        response_handler=response_handler,
        context_builder=context_builder,
    )


# ── fixtures: 分段回复路径 ─────────────────────────────────────────────────


@pytest.fixture()
def mock_agent_runtime_segmented():
    """Mock AgentRuntime.run_chat — 模拟分段路径（delivery_performed=True）"""
    from plugins.DicePP.module.persona.agent.runtime import AgentRuntime
    from plugins.DicePP.module.persona.agent.loop import AgentRunResult

    original = AgentRuntime.run_chat
    result = AgentRunResult(
        run_id="test", turn_id="test", status="completed",
        final_reason="terminal_final_segment", final_text="hello",
        delivery_performed=True,
        tool_rounds=2,
    )

    async def fake_run_chat(self, messages, user_id, group_id, **kwargs):
        return result

    AgentRuntime.run_chat = fake_run_chat
    yield result
    AgentRuntime.run_chat = original


@pytest.fixture
def seg_mock_store():
    store = MagicMock(spec=PersonaDataStore)
    store.get_group_messages = AsyncMock(return_value=[])
    store.get_recent_messages = AsyncMock(return_value=[])
    store.add_message_stream = AsyncMock(return_value=1)
    store.get_relationship = AsyncMock(return_value=None)
    store.get_user_profile = AsyncMock(return_value=None)
    store.get_user_llm_config = AsyncMock(return_value=None)
    store.get_daily_usage = AsyncMock(return_value=0)
    store.is_user_whitelisted = AsyncMock(return_value=False)
    store.is_group_whitelisted = AsyncMock(return_value=False)
    return store


@pytest.fixture
def seg_mock_port():
    return AsyncMock()


@pytest.fixture
def tool_registry():
    from plugins.DicePP.module.persona.tools.registry import ToolRegistry
    return ToolRegistry()


@pytest.fixture
def coordinator():
    return LLMCallCoordinator(max_failures=3, max_iterations=5)


@pytest.fixture
def seg_character():
    return Character(name="Test")


@pytest.fixture
def context_builder(seg_character):
    return ContextBuilder(seg_character)


@pytest.fixture
def seg_config():
    return ChatConfig(
        tools_max_rounds=5,
        segment_target_chars=30,
        segment_max_chars=80,
        segment_soft_limit=100,
        segment_hard_limit=120,
        segment_count_max=10,
        segment_max_delay=10.0,
        segment_round_callbacks_max=3,
    )


@pytest.fixture
def mock_router():
    router = MagicMock()
    router.increment_usage = AsyncMock()
    router.data_store = None
    router.quota_check_enabled = False
    router.daily_limit = 20
    router.trace_enabled = False
    router.trace_max_age_days = 7
    router.config = None
    return router


@pytest.fixture
def seg_session(seg_mock_store, mock_router, tool_registry, coordinator, seg_character, seg_config, context_builder, seg_mock_port, mock_agent_runtime_segmented):
    scoring_trigger = MagicMock()
    scoring_trigger.effective_relationship = MagicMock(side_effect=lambda rel: rel)
    scoring_trigger.on_interaction = AsyncMock()
    scoring_trigger.update_character = MagicMock()

    response_handler = MagicMock()
    response_handler.port = seg_mock_port
    response_handler.persist = AsyncMock(return_value=1)
    response_handler.send = AsyncMock(return_value=True)
    response_handler.persist_and_send = AsyncMock(return_value=1)

    return ChatSession(
        store=seg_mock_store,
        router=mock_router,
        tool_registry=tool_registry,
        coordinator=coordinator,
        character=seg_character,
        config=seg_config,
        scoring_trigger=scoring_trigger,
        response_handler=response_handler,
        context_builder=context_builder,
    )


# ── 去重 / 拒绝测试 ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dedup_within_5_seconds_returns_none(mock_agent_runtime):
    """5 秒内重复消息直接返回 None，不进入 LLM 路径"""
    session = _make_session()

    first = await session.chat("u1", "", "你好")
    assert first == "reply"

    # 立刻发送相同消息：应被去重
    again = await session.chat("u1", "", "你好")
    assert again is None
    # 第二次不应触发额外的 agent 调用（去重后正好 1 次）
    # 当前契约：第一次 chat 走 agent 产生 reply 并写入 _last_messages；第二次命中 dedup 直接 return None；agent 总调用次数恒为 1
    assert len(mock_agent_runtime) == 1


@pytest.mark.asyncio
async def test_dedup_different_message_not_skipped(mock_agent_runtime):
    """5 秒内不同消息正常处理"""
    session = _make_session()

    a = await session.chat("u1", "", "你好")
    b = await session.chat("u1", "", "再见")

    assert a == "reply"
    assert b == "reply"


@pytest.mark.asyncio
async def test_first_private_message_goes_through_coordinator(mock_agent_runtime):
    """私聊首次对话走标准 LLM 路径"""
    session = _make_session()
    result = await session.chat("u1", "", "你好")
    assert result == "reply"
    assert len(mock_agent_runtime) == 1


@pytest.mark.asyncio
async def test_first_group_message_goes_through_coordinator(mock_agent_runtime):
    """群聊首次对话走标准 LLM 路径"""
    session = _make_session()
    result = await session.chat("u1", "g1", "大家好")
    assert result == "reply"
    assert len(mock_agent_runtime) == 1


@pytest.mark.asyncio
async def test_refuse_triggers_at_low_reputation(mock_agent_runtime):
    """reputation < 30 时返回 refuse 文案，跳过 LLM"""
    rel = RelationshipState(
        user_id="u1",
        reputation=0.0,
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

    result = await session.chat("u1", "", "你好")

    assert result == "...（已读不回）"
    assert len(mock_agent_runtime) == 0


@pytest.mark.asyncio
async def test_refuse_does_not_trigger_above_reputation_threshold(mock_agent_runtime):
    """reputation >= 30 时不进入拒绝分支"""
    # 高信誉 → 不触发拒绝
    rel = RelationshipState(
        user_id="u1",
        reputation=50.0,
    )
    session = _make_session(
        refuse_enabled=True,
        relationship=rel,
        refuse_messages=["...（已读不回）"],
    )

    session.store.get_recent_messages = AsyncMock(return_value=[
        MagicMock(role="user", content="prev"),
    ])

    result = await session.chat("u1", "", "你好")

    assert result != "...（已读不回）"


# ── _apply_token_window 测试 ───────────────────────────────────────────────


class TestApplyTokenWindow:
    """_apply_token_window 行为测试"""

    def _make_gc(self, content, created_at, role="user", display_name="群友"):
        from plugins.DicePP.module.persona.data.models import UnifiedMessage
        from plugins.DicePP.core.message_types import MessageType
        return UnifiedMessage(
            user_id="u1",
            role=role,
            content=content,
            created_at=created_at,
            group_id="g1",
            display_name=display_name,
            type=MessageType.CHAT,
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

        now = wall_now()
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

        now = wall_now()
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

        now = wall_now()
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

        now = wall_now()
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

        now = wall_now()
        history = [
            self._make_gc("消息1", now),
            self._make_gc("消息2", now),
        ]
        result, truncated = session._apply_token_window(history)
        # 第一条因 result 为空而不被 budget break，保底保留 1 条
        assert len(result) == 1
        assert truncated is True


# ── ScoringTrigger decay 测试 ──────────────────────────────────────────────


class TestDecayInScoringTrigger:
    """ScoringTrigger.on_interaction 中的惰性 decay 路径测试"""

    @pytest.mark.asyncio
    async def test_on_interaction_applies_decay_when_due(self):
        """decay_calculator.should_apply_decay=True 时惰性应用衰减并写入 score_event"""
        from plugins.DicePP.module.persona.data.models import RelationshipState, ScoreDeltas
        from plugins.DicePP.module.persona.chat.scoring_trigger import ScoringTrigger

        store = AsyncMock()

        decay_calc = MagicMock()
        decay_calc.should_apply_decay = MagicMock(return_value=True)
        decay_calc.calculate_decay = MagicMock(return_value=(
            ScoreDeltas(intimacy=-3.0),
            0.5,
            "超过7天未互动",
        ))

        character = MagicMock()

        config = MagicMock()
        config.timezone = ""
        config.scoring_interval = 999

        trigger = ScoringTrigger(
            store=store,
            scoring_agent=MagicMock(),
            decay_calculator=decay_calc,
            character=character,
            config=config,
        )

        rel = RelationshipState(user_id="u1")
        store.get_relationship = AsyncMock(return_value=rel)
        store.get_familiarity_daily = AsyncMock(return_value=0.0)
        store.add_familiarity_daily = AsyncMock(return_value=0.6)
        store.update_relationship = AsyncMock()
        store.add_score_event = AsyncMock()
        store.init_relationship = AsyncMock(return_value=rel)

        await trigger.on_interaction("u1", "", "user_msg", "assistant_msg")

        decay_calc.should_apply_decay.assert_called_once()
        decay_calc.calculate_decay.assert_called_once()
        store.add_score_event.assert_awaited_once()
        # 验证写入的 score_event 包含 decay 原因
        event = store.add_score_event.call_args[0][0]
        assert "time_decay" in event.reason


# ── _build_diary_context 测试 ──────────────────────────────────────────────


class TestBuildDiaryContext:
    """测试 _build_diary_context 的 context_summary 选择逻辑"""

    @pytest.mark.asyncio
    async def test_uses_context_summary_when_present(self):
        """context_summary 非空时优先使用"""
        session = _make_session()
        session.store.get_daily_events = AsyncMock(return_value=[
            DailyEvent(date="2024-01-01", event_type="system",
                       description="长描述内容在这里很长很长很长",
                       context_summary="短摘要"),
        ])
        session.store.get_diary = AsyncMock(return_value=None)
        result = await session._build_diary_context()
        assert "短摘要" in result
        assert "长描述" not in result

    @pytest.mark.asyncio
    async def test_falls_back_to_description_when_context_summary_empty(self):
        """context_summary 为空时回退到 description"""
        session = _make_session()
        session.store.get_daily_events = AsyncMock(return_value=[
            DailyEvent(date="2024-01-01", event_type="system",
                       description="窗外下雨了",
                       context_summary=""),
        ])
        session.store.get_diary = AsyncMock(return_value=None)
        result = await session._build_diary_context()
        assert "窗外下雨了" in result

    @pytest.mark.asyncio
    async def test_filters_out_events_with_both_fields_empty(self):
        """context_summary 和 description 都为空时事件被过滤"""
        session = _make_session()
        session.store.get_daily_events = AsyncMock(return_value=[
            DailyEvent(date="2024-01-01", event_type="system",
                       description="   ", context_summary=""),
        ])
        session.store.get_diary = AsyncMock(return_value=None)
        result = await session._build_diary_context()
        assert result == ""  # 无有效事件，无日记

    @pytest.mark.asyncio
    async def test_mixed_scenario(self):
        """混合场景：摘要/回退/过滤同时存在"""
        session = _make_session()
        session.store.get_daily_events = AsyncMock(return_value=[
            DailyEvent(date="2024-01-01", event_type="system",
                       description="长描述A", context_summary="摘要A"),
            DailyEvent(date="2024-01-01", event_type="system",
                       description="长描述B", context_summary=""),
            DailyEvent(date="2024-01-01", event_type="system",
                       description="   ", context_summary=""),
        ])
        session.store.get_diary = AsyncMock(return_value=None)
        result = await session._build_diary_context()
        assert "摘要A" in result        # 使用 context_summary
        assert "长描述B" in result      # 回退到 description
        assert "长描述A" not in result  # context_summary 替代了 description

    @pytest.mark.asyncio
    async def test_falls_back_to_yesterday_diary_when_no_events(self):
        """无有效事件时回退到昨日日记"""
        session = _make_session()
        session.store.get_daily_events = AsyncMock(return_value=[
            DailyEvent(date="2024-01-01", event_type="system",
                       description="   ", context_summary=""),
        ])
        session.store.get_diary = AsyncMock(return_value="昨天去了公园")
        result = await session._build_diary_context()
        assert "昨天" in result
        assert "公园" in result


class TestCollectEventNotifications:
    """_collect_event_notifications — 增量事件通知 + 时间窗口过滤"""

    @pytest.fixture
    def _session_with_sm(self):
        """构造带 session_manager 的 ChatSession，用于 _collect_event_notifications 测试"""
        from plugins.DicePP.module.persona.chat.session_manager import SessionManager
        from plugins.DicePP.module.persona.chat.chat_config import ChatConfig

        session = _make_session()
        sm = SessionManager(
            store=session.store,
            config=ChatConfig(),
        )
        session.session_manager = sm
        session.store.get_daily_events = AsyncMock(return_value=[])
        return session

    # 共享时间常量：now 是「当前时间」，context_since 是「上次上下文更新时间」，
    # evt_time 介于两者之间，使事件落在注入窗口内
    _NOW = datetime(2026, 1, 1, 12, 0, 0)
    _CTX_SINCE = datetime(2026, 1, 1, 10, 0, 0)
    _EVT_TIME = datetime(2026, 1, 1, 11, 0, 0)

    @pytest.mark.asyncio
    async def test_new_events_generate_notifications(self, _session_with_sm):
        """新事件在注入窗口内时生成带时间前缀的 [通知]"""
        session = _session_with_sm
        tracker = session.session_manager.get_tracker("scope1")
        tracker["last_context_update_at"] = self._CTX_SINCE

        session.store.get_daily_events = AsyncMock(return_value=[
            DailyEvent(id=1, date="2026-01-01", event_type="system",
                       description="描述A", context_summary="摘要A",
                       created_at=self._EVT_TIME),
            DailyEvent(id=2, date="2026-01-01", event_type="system",
                       description="描述B", context_summary="",
                       created_at=self._EVT_TIME),
        ])

        notes, events = await session._collect_event_notifications("scope1", self._NOW)
        assert len(notes) == 2
        assert "[通知][11:00 1小时前] 摘要A" == notes[0]
        assert "[通知][11:00 1小时前] 描述B" == notes[1]
        assert tracker["last_context_update_at"] == self._NOW

    @pytest.mark.asyncio
    async def test_already_notified_events_skipped(self, _session_with_sm):
        """已在 notified_event_ids 中的事件不再通知"""
        session = _session_with_sm
        tracker = session.session_manager.get_tracker("scope1")
        tracker["notified_event_ids"] = {1}
        tracker["last_context_update_at"] = self._CTX_SINCE

        session.store.get_daily_events = AsyncMock(return_value=[
            DailyEvent(id=1, date="2026-01-01", event_type="system",
                       description="已通知", context_summary="已通知摘要",
                       created_at=self._EVT_TIME),
            DailyEvent(id=2, date="2026-01-01", event_type="system",
                       description="新事件", context_summary="新摘要",
                       created_at=self._EVT_TIME),
        ])

        notes, events = await session._collect_event_notifications("scope1", self._NOW)
        assert len(notes) == 1
        assert "[通知][11:00 1小时前] 新摘要" == notes[0]
        assert tracker["notified_event_ids"] == {1, 2}

    @pytest.mark.asyncio
    async def test_cross_day_resets_notified_ids_keeps_context_since(self, _session_with_sm):
        """跨天时 notified_event_ids 重置，但 last_context_update_at 保留（仍作窗口下界）"""
        session = _session_with_sm
        tracker = session.session_manager.get_tracker("scope1")
        tracker["notified_event_ids"] = {1, 2, 3}
        tracker["last_event_notification_date"] = "2026-06-01"
        tracker["last_context_update_at"] = datetime(2026, 1, 1, 10, 0, 0)

        session.store.get_daily_events = AsyncMock(return_value=[
            DailyEvent(id=4, date="2026-01-01", event_type="system",
                       description="新年事件", context_summary="新年摘要",
                       created_at=datetime(2026, 1, 1, 11, 0, 0)),
        ])

        notes, events = await session._collect_event_notifications("scope1", self._NOW)
        # 跨天：notified_event_ids 被重置 → {4}；事件在 context_since 之后 → 注入
        assert len(notes) == 1
        assert tracker["notified_event_ids"] == {4}

    @pytest.mark.asyncio
    async def test_no_events_returns_empty(self, _session_with_sm):
        """没有事件时返回空列表"""
        session = _session_with_sm
        session.store.get_daily_events = AsyncMock(return_value=[])

        notes, events = await session._collect_event_notifications("scope1", self._NOW)
        assert notes == []

    @pytest.mark.asyncio
    async def test_same_day_no_reset(self, _session_with_sm):
        """同一天多次调用不会 reset notified_event_ids"""
        session = _session_with_sm
        tracker = session.session_manager.get_tracker("scope1")
        tracker["last_event_notification_date"] = "2026-01-01"
        tracker["notified_event_ids"] = {1}
        tracker["last_context_update_at"] = self._CTX_SINCE

        session.store.get_daily_events = AsyncMock(return_value=[
            DailyEvent(id=1, date="2026-01-01", event_type="system",
                       description="已通知", context_summary="已通知摘要",
                       created_at=self._EVT_TIME),
            DailyEvent(id=2, date="2026-01-01", event_type="system",
                       description="新来", context_summary="新来摘要",
                       created_at=self._EVT_TIME),
        ])

        notes, events = await session._collect_event_notifications("scope1", self._NOW)
        assert len(notes) == 1
        assert tracker["notified_event_ids"] == {1, 2}

    @pytest.mark.asyncio
    async def test_first_call_with_none_last_date(self, _session_with_sm):
        """首次调用时 last_context_update_at 为 None，旧事件静默标记不注入"""
        session = _session_with_sm
        tracker = session.session_manager.get_tracker("scope1")
        # 模拟首次调用：tracker 默认值中 last_event_notification_date 为 None
        assert tracker.get("last_event_notification_date") is None
        assert tracker.get("last_context_update_at") is None

        session.store.get_daily_events = AsyncMock(return_value=[
            DailyEvent(id=1, date="2026-06-05", event_type="system",
                       description="首个事件", context_summary="首个摘要",
                       created_at=datetime(2026, 6, 5, 8, 0, 0)),
        ])

        notes, events = await session._collect_event_notifications("scope1", datetime(2026, 6, 5, 12, 0, 0))
        # context_since 为 None → 旧事件不注入
        assert len(notes) == 0
        # 但事件 ID 已静默标记为已见
        assert tracker["notified_event_ids"] == {1}
        assert tracker["last_event_notification_date"] == "2026-06-05"
        assert tracker["last_context_update_at"] == datetime(2026, 6, 5, 12, 0, 0)

    @pytest.mark.asyncio
    async def test_event_before_context_since_is_skipped(self, _session_with_sm):
        """created_at <= context_since 的事件不注入，仅静默标记"""
        session = _session_with_sm
        tracker = session.session_manager.get_tracker("scope1")
        tracker["last_context_update_at"] = datetime(2026, 1, 1, 15, 0, 0)  # 晚于事件时间

        session.store.get_daily_events = AsyncMock(return_value=[
            DailyEvent(id=1, date="2026-01-01", event_type="system",
                       description="早上事件", context_summary="早摘要",
                       created_at=datetime(2026, 1, 1, 8, 0, 0)),
            DailyEvent(id=2, date="2026-01-01", event_type="system",
                       description="下午事件", context_summary="午摘要",
                       created_at=datetime(2026, 1, 1, 16, 0, 0)),
        ])

        notes, events = await session._collect_event_notifications("scope1", self._NOW)
        # id=1: created_at(8:00) <= context_since(15:00) → 不注入
        # id=2: created_at(16:00) > context_since(15:00) → 注入
        assert len(notes) == 1
        assert "午摘要" in notes[0]
        # 两个事件 ID 都被标记
        assert tracker["notified_event_ids"] == {1, 2}

    @pytest.mark.asyncio
    async def test_event_without_created_at_is_skipped(self, _session_with_sm):
        """created_at 为 None 的事件不注入（positive condition 要求 created_at 不为空）"""
        session = _session_with_sm
        tracker = session.session_manager.get_tracker("scope1")
        tracker["last_context_update_at"] = self._CTX_SINCE

        session.store.get_daily_events = AsyncMock(return_value=[
            DailyEvent(id=1, date="2026-01-01", event_type="system",
                       description="无时间事件", context_summary="无时间摘要",
                       created_at=None),
        ])

        notes, events = await session._collect_event_notifications("scope1", self._NOW)
        assert len(notes) == 0
        assert tracker["notified_event_ids"] == {1}


# ── 分段回复集成测试（原 test_chat_session_segmented.py）────────────────────


class TestFlagLifecycle:
    @pytest.mark.asyncio
    async def test_delivery_performed_flag_set_by_chat_with_tools(self, seg_session):
        result_text, dp = await seg_session._chat_with_tools("u1", "", [{"role": "user", "content": "hi"}])
        assert result_text == "hello"
        assert dp is True

    @pytest.mark.asyncio
    async def test_chat_via_coordinator_returns_empty_str_for_delivery_performed(self, seg_session):
        result = await seg_session._chat_via_coordinator("u1", "", "hi", "user:u1")
        assert result == ""

    @pytest.mark.asyncio
    async def test_exception_does_not_produce_sentinel(self, seg_session):
        from plugins.DicePP.module.persona.agent.runtime import AgentRuntime
        original = AgentRuntime.run_chat

        async def failing_run_chat(self, messages, user_id, group_id, **kwargs):
            raise RuntimeError("boom")

        AgentRuntime.run_chat = failing_run_chat
        try:
            with pytest.raises(RuntimeError):
                await seg_session._chat_with_tools("u1", "", [{"role": "user", "content": "hi"}])
        finally:
            AgentRuntime.run_chat = original


class TestReturnSemantics:
    @pytest.mark.asyncio
    async def test_chat_returns_empty_str_for_delivery_performed(self, seg_session):
        # AgentRuntime mock returns delivery_performed=True → 空字符串
        result = await seg_session.chat("u1", "", "hello")
        assert result == ""

    @pytest.mark.asyncio
    async def test_chat_returns_empty_str_for_segmented_mode(self, seg_session):
        """分段模式下 chat 返回空字符串"""
        result = await seg_session.chat("u1", "", "hello")
        assert result == ""


# ── 计费路径测试 ───────────────────────────────────────────────────────────


class TestChargingPath:
    """确保"一次 LLM 调用 = 一次 increment_usage"不变量"""

    @pytest.fixture(autouse=True)
    def mock_agent_runtime(self):
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

    @pytest.mark.asyncio
    async def test_single_call_charges_once(self):
        """单次成功调用 → 1 次扣费"""
        coordinator = LLMCallCoordinator()
        session = _make_session(coordinator=coordinator)

        result = await session.chat("u1", "", "你好")

        assert result == "reply"
        session.router.increment_usage.assert_awaited_once_with("u1")

    @pytest.mark.asyncio
    async def test_buffered_merge_charges_per_call(self):
        """N 次 LLM 调用（中间轮 + 最终轮）→ N 次扣费（中间轮 on_result + 最终轮 success 各扣 1 次）"""
        coordinator = LLMCallCoordinator()
        session = _make_session(coordinator=coordinator)

        llm_calls = []
        first_started = asyncio.Event()
        release_first = asyncio.Event()

        async def slow_chat_call(user_id, group_id, messages, **kwargs):
            llm_calls.append((user_id, group_id, messages))
            call_index = len(llm_calls)
            # 模拟 AgentRuntime 内部计费行为
            await session.router.increment_usage(user_id)
            if call_index == 1:
                first_started.set()
                await release_first.wait()
            return f"reply_{call_index}"

        session._coordinator_chat_call_fn = slow_chat_call

        first_task = asyncio.create_task(session.chat("u1", "", "msg1"))
        await asyncio.wait_for(first_started.wait(), timeout=1.0)

        async def buffered(message):
            await first_started.wait()
            return await session.chat("u1", "", message)

        buffered_results = await asyncio.gather(
            buffered("msg2"),
            buffered("msg3"),
        )
        assert buffered_results == [None, None]

        release_first.set()
        await first_task

        # 至少 2 次 LLM 调用（首轮 + 至少 1 次 buffered 合并）
        assert len(llm_calls) >= 2
        # 中间轮通过 on_result 各扣 1 次 + 最终轮 success 1 次：N 次 LLM 调用 → N 次扣费
        charged_user_ids = [
            call.args[0]
            for call in session.router.increment_usage.await_args_list
        ]
        assert charged_user_ids == ["u1"] * len(llm_calls)

    @pytest.mark.asyncio
    async def test_all_failures_does_not_charge(self):
        """全部失败走 on_exhausted → 0 次扣费"""
        coordinator = LLMCallCoordinator(max_failures=1, max_iterations=5)
        session = _make_session(coordinator=coordinator)

        async def always_fail(user_id, group_id, messages, **kwargs):
            raise RuntimeError("LLM down")

        session._coordinator_chat_call_fn = always_fail

        result = await session.chat("u1", "", "msg")

        # 兜底文案
        assert "暂时不可用" in result
        session.router.increment_usage.assert_not_awaited()


# ── Level 1: 跨调用状态隔离 ──────────────────────────────────────────────────


@contextmanager
def _patch_run_chat_seq(results: list):
    """Mock AgentRuntime.run_chat 按序返回 results。

    results 中非 Exception 项应均为 AgentRunResult。
    Exception 项会在 run_chat 中被抛出。

    使用 contextmanager 确保无论测试是否抛出异常，最终均恢复原始 run_chat。
    """
    from plugins.DicePP.module.persona.agent.runtime import AgentRuntime

    original = AgentRuntime.run_chat
    idx = 0

    async def fake_run_chat(self, messages, user_id, group_id, **kwargs):
        nonlocal idx
        if idx >= len(results):
            raise RuntimeError(f"mock exhausted: only {len(results)} results")
        item = results[idx]
        idx += 1
        if isinstance(item, Exception):
            raise item
        return item

    AgentRuntime.run_chat = fake_run_chat
    try:
        yield idx  # number of calls consumed
    finally:
        AgentRuntime.run_chat = original


def _make_result(**kw) -> "AgentRunResult":
    from plugins.DicePP.module.persona.agent.loop import AgentRunResult

    defaults = dict(
        run_id="test", turn_id="test", status="completed",
        final_reason="direct_content", final_text="reply",
        delivery_performed=False,
    )
    defaults.update(kw)
    return AgentRunResult(**defaults)


class TestCrossCallStateIsolation:
    """同一 ChatSession 实例上连续多次 chat() 调用不互相污染"""

    @pytest.mark.asyncio
    async def test_seg_normal_seg_does_not_leak(self):
        """场景 1: 分段→普通→分段 — delivery_performed 不污染后续"""
        session = _make_session()

        with _patch_run_chat_seq([
            _make_result(delivery_performed=True, final_reason="terminal_final_segment", final_text="seg1"),
            _make_result(delivery_performed=False, final_text="normal"),
            _make_result(delivery_performed=True, final_reason="terminal_final_segment", final_text="seg2"),
        ]):
            r1 = await session.chat("u1", "", "msg1")
            assert r1 == ""  # delivery_performed → 空字符串

            r2 = await session.chat("u1", "", "msg2")
            assert r2 == "normal"

            r3 = await session.chat("u1", "", "msg3")
            assert r3 == ""  # delivery_performed → 空字符串

    @pytest.mark.asyncio
    async def test_normal_seg_normal_does_not_leak(self):
        """场景 2: 普通→分段→普通 — 对称验证"""
        session = _make_session()

        with _patch_run_chat_seq([
            _make_result(delivery_performed=False, final_text="normal1"),
            _make_result(delivery_performed=True, final_reason="terminal_final_segment", final_text="seg"),
            _make_result(delivery_performed=False, final_text="normal2"),
        ]):
            assert await session.chat("u1", "", "msg1") == "normal1"
            assert await session.chat("u1", "", "msg2") == ""
            assert await session.chat("u1", "", "msg3") == "normal2"

    @pytest.mark.asyncio
    async def test_dedup_then_normal(self):
        """场景 3: 去重→正常 — 去重返回 None 后下一条正常"""
        session = _make_session()

        with _patch_run_chat_seq([
            _make_result(final_text="first"),
            _make_result(final_text="second"),
        ]):
            r1 = await session.chat("u1", "", "hello")
            assert r1 == "first"

            # 5 秒内相同消息 → 去重
            r2 = await session.chat("u1", "", "hello")
            assert r2 is None

            # 不同消息 → 正常
            r3 = await session.chat("u1", "", "world")
            assert r3 == "second"

    @pytest.mark.asyncio
    async def test_exception_then_normal(self):
        """场景 4: 异常→正常 — LLM 抛异常后恢复"""
        # 使用 max_failures=1 使得单次 call_fn 异常立即触发 on_exhausted
        coordinator = LLMCallCoordinator(max_failures=1, max_iterations=5)
        session = _make_session(coordinator=coordinator)

        with _patch_run_chat_seq([
            RuntimeError("LLM down"),
            _make_result(final_text="recovered"),
        ]):
            r1 = await session.chat("u1", "", "msg1")
            assert "暂时不可用" in r1

            r2 = await session.chat("u1", "", "msg2")
            assert r2 == "recovered"

    @pytest.mark.asyncio
    async def test_refuse_then_normal(self):
        """场景 5: 拒绝→正常 — reputation < 30 触发拒绝后，关掉拒绝功能，下条正常"""
        from plugins.DicePP.module.persona.data.models import RelationshipState

        rel = RelationshipState(
            user_id="u1", reputation=0.0,
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

        with _patch_run_chat_seq([
            _make_result(final_text="normal_reply"),
        ]):
            r1 = await session.chat("u1", "", "msg1")
            assert r1 == "...（已读不回）"

            # 关闭拒绝 → 下条正常进入 LLM
            session.config.relationship_refuse_enabled = False
            r2 = await session.chat("u1", "", "msg2")
            assert r2 == "normal_reply"

    @pytest.mark.asyncio
    async def test_sleep_then_awake(self):
        """场景 6: 睡眠→唤醒 — is_awake=False 的睡眠消息不污染后续"""
        sleep_gate = MagicMock()
        sleep_gate.is_awake = AsyncMock(return_value=False)

        session = _make_session()
        session._sleep_gate = sleep_gate
        session.character.extensions.sleep_messages = ["角色正在休息..."]

        with _patch_run_chat_seq([
            _make_result(final_text="awake_reply"),
        ]):
            r1 = await session.chat("u1", "", "msg1")
            assert r1 == "角色正在休息..."

            # 唤醒
            sleep_gate.is_awake = AsyncMock(return_value=True)
            r2 = await session.chat("u1", "", "msg2")
            assert r2 == "awake_reply"

    @pytest.mark.asyncio
    async def test_buffered_then_standalone(self):
        """场景 7: 缓冲合并→独立消息 — 并发触发 coordinator 缓冲后，独立消息正常"""
        coordinator = LLMCallCoordinator(max_iterations=10)
        session = _make_session(coordinator=coordinator)

        first_started = asyncio.Event()
        release_first = asyncio.Event()
        call_count = 0

        async def slow_coordinator_call(user_id, group_id, messages, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                first_started.set()
                await release_first.wait()
            return f"reply_{call_count}"

        # 保存原始方法引用，测试结束后恢复
        original_fn = session._coordinator_chat_call_fn
        session._coordinator_chat_call_fn = slow_coordinator_call

        # 第一条消息启动 coordinator
        task1 = asyncio.create_task(session.chat("u1", "", "msg1"))
        await asyncio.wait_for(first_started.wait(), timeout=1.0)

        # 并发发送 → 被缓冲
        r2, r3 = await asyncio.gather(
            session.chat("u1", "", "msg2"),
            session.chat("u1", "", "msg3"),
        )
        assert r2 is None
        assert r3 is None

        # 释放第一条，让 coordinator 继续处理缓冲
        release_first.set()
        r1 = await task1
        assert r1 is not None

        # 缓冲处理完后，独立消息正常
        session._coordinator_chat_call_fn = original_fn
        with _patch_run_chat_seq([_make_result(final_text="standalone")]):
            r4 = await session.chat("u1", "", "msg4")
            assert r4 == "standalone"

    @pytest.mark.asyncio
    async def test_coordinator_exhausted_then_normal(self):
        """场景 8: coordinator 耗尽→正常 — max_failures=1 导致首次失败即 on_exhausted，之后恢复"""
        coordinator = LLMCallCoordinator(max_failures=1, max_iterations=5)
        session = _make_session(coordinator=coordinator)

        call_count = 0

        async def fail_then_recover(user_id, group_id, messages, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("fail_1")
            return f"recovered_{call_count}"

        original_fn = session._coordinator_chat_call_fn
        session._coordinator_chat_call_fn = fail_then_recover

        # 第一次失败触发 on_exhausted
        r1 = await session.chat("u1", "", "msg1")
        assert "暂时不可用" in r1

        # 第二次恢复
        r2 = await session.chat("u1", "", "msg2")
        assert "recovered" in r2

        session._coordinator_chat_call_fn = original_fn

    @pytest.mark.asyncio
    async def test_multi_user_alternating(self):
        """场景 9: 多用户交替 — 不同 user_id 交替调用，互不干扰"""
        session = _make_session()

        with _patch_run_chat_seq([
            _make_result(final_text="reply_u1_a"),
            _make_result(final_text="reply_u2_a"),
            _make_result(final_text="reply_u1_b"),
        ]):
            r1 = await session.chat("u1", "", "hi from u1")
            assert r1 == "reply_u1_a"

            r2 = await session.chat("u2", "", "hi from u2")
            assert r2 == "reply_u2_a"

            r3 = await session.chat("u1", "", "u1 again")
            assert r3 == "reply_u1_b"

            # 去重 key 按 user_id:group_id 隔离，不同用户不互相影响
