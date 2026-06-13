"""SessionManager 单元测试 — get_or_create / compact_session / SessionDecision"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone as tz, timedelta

import pytest

from plugins.DicePP.module.persona.chat.session_manager import (
    SessionManager,
    SessionDecision,
    _INFINITE_GAP_SECONDS,
    _make_time_notification,
)
from plugins.DicePP.module.persona.chat.chat_config import ChatConfig
from plugins.DicePP.module.persona.data.models import PersonaSession, UnifiedMessage
from plugins.DicePP.core.message_types import MessageType


def _make_session(**overrides) -> PersonaSession:
    defaults = {
        "session_id": 1,
        "user_id": "u1",
        "character_id": "test_char",
        "static_prompt": "static base",
        "static_hash": "abc123",
        "token_budget": 64000,
        "token_estimate": 0,
        "status": "active",
        "last_active_at": datetime.now(tz.utc),
    }
    defaults.update(overrides)
    return PersonaSession(**defaults)


def _now():
    return datetime.now(tz.utc)


class TestMakeTimeNotification:
    def test_formats_chinese_weekday(self):
        note = _make_time_notification("Asia/Shanghai")
        assert "[通知][" in note and "现在是" in note
        valid_weekdays = {"周一", "周二", "周三", "周四", "周五", "周六", "周日"}
        assert any(wd in note for wd in valid_weekdays)


class TestGetOrCreate:
    """mock store，覆盖冷启动 / gap 超时 / hash 变化三条分支"""

    @pytest.fixture
    def store(self):
        s = AsyncMock()
        s.get_active_session = AsyncMock(return_value=None)
        s.create_session = AsyncMock()
        s.update_session = AsyncMock()
        s.get_session_messages = AsyncMock(return_value=[])
        s.add_session_messages = AsyncMock()
        s.get_session_by_id = AsyncMock()
        s.delete_session = AsyncMock()
        return s

    @pytest.fixture
    def mgr(self, store):
        config = ChatConfig()
        return SessionManager(store=store, config=config)

    @pytest.mark.asyncio
    async def test_cold_start_creates_session(self, mgr, store):
        """冷启动：无活跃 session → 创建新 session + 时间通知"""
        new_session = _make_session(session_id=99)
        store.create_session.return_value = new_session

        decision = await mgr.get_or_create(
            user_id="u1", character_id="char1",
            static_prompt="prompt", static_hash="hash1",
        )

        assert decision.is_new is True
        assert decision.static_rebuilt is False
        assert decision.session.session_id == 99
        assert len(decision.notifications) == 1
        assert "[通知][" in decision.notifications[0] and "现在是" in decision.notifications[0]

    @pytest.mark.asyncio
    async def test_cold_start_clears_tracker(self, mgr, store):
        """冷启动时清理旧 tracker"""
        # Pre-populate tracker
        mgr._trackers["u1"] = {"last_relation_label": "友好"}
        new_session = _make_session(session_id=1)
        store.create_session.return_value = new_session

        await mgr.get_or_create("u1", "char1", "prompt", "hash1")

        # tracker should be cleared
        assert "u1" not in mgr._trackers

    @pytest.mark.asyncio
    async def test_gap_expired_creates_new(self, mgr, store):
        """gap 超时 → 归档旧 session + 创建新 session"""
        old_session = _make_session(
            session_id=1,
            last_active_at=_now() - timedelta(days=2),  # gap > 86400
        )
        new_session = _make_session(session_id=2)
        store.get_active_session.return_value = old_session
        store.create_session.return_value = new_session

        decision = await mgr.get_or_create("u1", "char1", "prompt", "hash1")

        assert decision.is_new is True
        assert decision.session.session_id == 2

    @pytest.mark.asyncio
    async def test_static_hash_changed_rebuilds(self, mgr, store):
        """static_hash 变化 → 更新静态基座，不创建新 session"""
        old_session = _make_session(
            session_id=1, static_hash="old_hash",
            last_active_at=_now() - timedelta(minutes=5),  # within gap
        )
        store.get_active_session.return_value = old_session

        decision = await mgr.get_or_create(
            "u1", "char1", static_prompt="new prompt", static_hash="new_hash",
        )

        assert decision.is_new is False
        assert decision.static_rebuilt is True
        assert decision.session.session_id == 1
        store.update_session.assert_called()

    @pytest.mark.asyncio
    async def test_normal_reuse(self, mgr, store):
        """正常复用：hash 相同、gap 未超时 → 复用 session"""
        old_session = _make_session(
            session_id=1, static_hash="hash1",
            last_active_at=_now() - timedelta(minutes=5),
        )
        store.get_active_session.return_value = old_session

        decision = await mgr.get_or_create("u1", "char1", "prompt", "hash1")

        assert decision.is_new is False
        assert decision.static_rebuilt is False
        assert decision.session.session_id == 1


class TestBackfillContext:
    """冷启动回填 — 群聊用 get_group_messages，私聊用 get_recent_messages"""

    @pytest.fixture
    def store(self):
        s = AsyncMock()
        s.get_active_session = AsyncMock(return_value=None)
        s.create_session = AsyncMock()
        s.update_session = AsyncMock()
        s.get_session_messages = AsyncMock(return_value=[])
        s.add_session_messages = AsyncMock()
        s.get_session_by_id = AsyncMock()
        s.delete_session = AsyncMock()
        s.get_group_messages = AsyncMock(return_value=[])
        s.get_recent_messages = AsyncMock(return_value=[])
        return s

    @pytest.fixture
    def mgr(self, store):
        config = ChatConfig()
        return SessionManager(store=store, config=config)

    @pytest.mark.asyncio
    async def test_group_cold_start_backfills_from_group_messages(self, mgr, store):
        """群聊冷启动：回填 get_group_messages 的返回结果"""
        now = datetime.now(tz.utc)
        recent = [
            UnifiedMessage(
                user_id="u2", group_id="g1", role="user", type=MessageType.AMBIENT,
                content="刚看演唱会回来", display_name="梨子", created_at=now - timedelta(minutes=5),
            ),
            UnifiedMessage(
                user_id="u1", group_id="g1", role="user", type=MessageType.CHAT,
                content="6", display_name="Emiya", created_at=now - timedelta(minutes=2),
            ),
        ]
        store.get_group_messages.return_value = recent
        new_session = _make_session(session_id=1)
        store.create_session.return_value = new_session

        decision = await mgr.get_or_create(
            user_id="g1", character_id="char1",
            static_prompt="prompt", static_hash="hash1",
            is_group=True,
        )

        assert decision.is_new is True
        store.get_group_messages.assert_called_once_with("g1", limit=20)
        store.get_recent_messages.assert_not_called()
        # 回填后 add_session_messages 被调用
        add_call_args = store.add_session_messages.call_args
        assert add_call_args is not None
        msgs = add_call_args[0][1]
        assert len(msgs) == 2
        # 第一条是 ambient 消息，带时间戳
        assert "刚看演唱会回来" in msgs[0].content
        assert "[梨子]" in msgs[0].content
        # 保留了原始 created_at
        assert msgs[0].created_at == recent[0].created_at

    @pytest.mark.asyncio
    async def test_private_cold_start_backfills_from_recent_messages(self, mgr, store):
        """私聊冷启动：回填 get_recent_messages 的返回结果"""
        now = datetime.now(tz.utc)
        recent = [
            UnifiedMessage(
                user_id="u1", group_id="", role="user", type=MessageType.CHAT,
                content="你好", display_name="", created_at=now - timedelta(minutes=3),
            ),
        ]
        store.get_recent_messages.return_value = recent
        new_session = _make_session(session_id=1)
        store.create_session.return_value = new_session

        decision = await mgr.get_or_create(
            user_id="u1", character_id="char1",
            static_prompt="prompt", static_hash="hash1",
            is_group=False,
        )

        assert decision.is_new is True
        store.get_recent_messages.assert_called_once_with("u1", "", limit=20)
        store.get_group_messages.assert_not_called()
        add_call_args = store.add_session_messages.call_args
        assert add_call_args is not None
        msgs = add_call_args[0][1]
        assert len(msgs) == 1
        assert "你好" in msgs[0].content

    @pytest.mark.asyncio
    async def test_backfill_empty_skips(self, mgr, store):
        """回填结果为空时不写入任何消息"""
        new_session = _make_session(session_id=1)
        store.create_session.return_value = new_session

        await mgr.get_or_create(
            user_id="g1", character_id="char1",
            static_prompt="prompt", static_hash="hash1",
            is_group=True,
        )

        # 回填为空时不应有任何消息写入
        store.add_session_messages.assert_not_called()

    @pytest.mark.asyncio
    async def test_gap_expired_also_backfills(self, mgr, store):
        """gap 超时新建 session 也会回填"""
        now = datetime.now(tz.utc)
        old_session = _make_session(
            session_id=1,
            last_active_at=now - timedelta(days=2),
        )
        recent = [
            UnifiedMessage(
                user_id="u2", group_id="g1", role="user", type=MessageType.CHAT,
                content="hello", display_name="Alice", created_at=now - timedelta(minutes=10),
            ),
        ]
        store.get_active_session.return_value = old_session
        store.get_group_messages.return_value = recent
        new_session = _make_session(session_id=2)
        store.create_session.return_value = new_session

        decision = await mgr.get_or_create(
            user_id="g1", character_id="char1",
            static_prompt="prompt", static_hash="hash1",
            is_group=True,
        )

        assert decision.is_new is True
        assert decision.session.session_id == 2
        store.get_group_messages.assert_called_once_with("g1", limit=20)

    @pytest.mark.asyncio
    async def test_normal_reuse_skips_backfill(self, mgr, store):
        """正常复用 session 时不触发回填"""
        now = datetime.now(tz.utc)
        old_session = _make_session(
            session_id=1, static_hash="hash1",
            last_active_at=now - timedelta(minutes=5),
        )
        store.get_active_session.return_value = old_session

        await mgr.get_or_create(
            user_id="g1", character_id="char1",
            static_prompt="prompt", static_hash="hash1",
            is_group=True,
        )

        store.get_group_messages.assert_not_called()
        store.get_recent_messages.assert_not_called()


class TestShutdown:
    """SessionManager.shutdown — 取消所有后台任务并等待完成。"""

    @pytest.fixture
    def store(self):
        s = AsyncMock()
        s.get_active_session = AsyncMock(return_value=None)
        s.create_session = AsyncMock()
        s.update_session = AsyncMock()
        s.get_session_messages = AsyncMock(return_value=[])
        s.add_session_messages = AsyncMock()
        s.get_session_by_id = AsyncMock()
        s.delete_session = AsyncMock()
        return s

    @pytest.fixture
    def mgr(self, store):
        config = ChatConfig()
        return SessionManager(store=store, config=config)

    @pytest.mark.asyncio
    async def test_shutdown_cancels_background_tasks(self, mgr):
        """shutdown 取消所有未完成的 background task。"""
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def slow_task():
            started.set()
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                cancelled.set()
                raise

        task = asyncio.ensure_future(slow_task())
        mgr._bg_tasks.add(task)
        await asyncio.wait_for(started.wait(), timeout=1.0)

        await mgr.shutdown()

        assert cancelled.is_set()
        assert task.done()

    @pytest.mark.asyncio
    async def test_shutdown_no_tasks_is_safe(self, mgr):
        """shutdown 在没有 background task 时安全返回。"""
        await mgr.shutdown()

    @pytest.mark.asyncio
    async def test_shutdown_completed_tasks_skipped(self, mgr):
        """已完成的任务不被 shutdown 取消"""
        async def quick_task():
            pass

        task = asyncio.ensure_future(quick_task())
        await task
        mgr._bg_tasks.add(task)

        await mgr.shutdown()
        # task 已完成，shutdown 后不变
        assert task.done()


class TestCompactSession:
    """mock store + router，覆盖 LLM 成功 / 失败 / 无 router 三条路径"""

    @pytest.fixture
    def store(self):
        s = AsyncMock()
        s.get_session_by_id = AsyncMock()
        s.get_session_messages = AsyncMock(return_value=[])
        s.create_session = AsyncMock()
        s.update_session = AsyncMock()
        s.delete_session = AsyncMock()
        s.add_session_messages = AsyncMock()
        return s

    @pytest.fixture
    def mgr(self, store):
        config = ChatConfig()
        return SessionManager(store=store, config=config)

    @pytest.mark.asyncio
    async def test_nonexistent_session_returns_false(self, mgr, store):
        store.get_session_by_id.return_value = None
        ok, text, new_id = await mgr.compact_session(999)
        assert ok is False
        assert text is None

    @pytest.mark.asyncio
    async def test_few_messages_skips_compression(self, mgr, store):
        """不足 MIN_COMPRESS_MSGS 条消息 → 跳过压缩，不删除 session"""
        session = _make_session(session_id=1)
        store.get_session_by_id.return_value = session
        store.get_session_messages.return_value = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]

        ok, text, new_id = await mgr.compact_session(1)

        assert ok is False
        assert text is None
        store.delete_session.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_router_hard_truncation(self, mgr, store):
        """无 router → 硬截断，创建新 session"""
        session = _make_session(session_id=1)
        new_session = _make_session(session_id=2)
        # 10 条 user/assistant 交替消息（5 轮）
        msgs = []
        for i in range(5):
            msgs.append({"role": "user", "content": f"u{i}"})
            msgs.append({"role": "assistant", "content": f"a{i}"})
        store.get_session_by_id.return_value = session
        store.get_session_messages.return_value = msgs
        store.create_session.return_value = new_session

        ok, text, new_id = await mgr.compact_session(1, router=None)

        assert ok is True
        assert "之前对话的摘要" in text or "部分历史已丢弃" in text
        store.create_session.assert_called_once()
        # 旧 session 应归档
        store.update_session.assert_any_call(1, status="archived")

    @pytest.mark.asyncio
    async def test_llm_success(self, mgr, store):
        """LLM 摘要成功 → 创建新 session + 摘要注入"""
        session = _make_session(session_id=1)
        new_session = _make_session(session_id=2)
        # 需要足够多消息确保 old_msgs 非空（KEEP_RECENT=10）
        msgs = [{"role": "user", "content": f"u{i}"} for i in range(10)]
        msgs += [{"role": "assistant", "content": f"a{i}"} for i in range(10)]
        store.get_session_by_id.return_value = session
        store.get_session_messages.return_value = msgs
        store.create_session.return_value = new_session

        router = AsyncMock()
        router.build_candidates = MagicMock(return_value=[("p1", "m1")])
        provider = AsyncMock()
        resp = MagicMock()
        resp.content = "这是一段测试摘要"
        provider.generate = AsyncMock(return_value=resp)
        router.get_model_provider = MagicMock(return_value=provider)

        ok, text, new_id = await mgr.compact_session(1, router=router)

        assert ok is True
        assert "测试摘要" in text
        provider.generate.assert_called_once()

    @pytest.mark.asyncio
    async def test_llm_failure_falls_back_to_hard_truncation(self, mgr, store):
        """LLM 失败 → 硬截断兜底"""
        session = _make_session(session_id=1)
        new_session = _make_session(session_id=2)
        msgs = [{"role": "user", "content": f"u{i}"} for i in range(10)]
        msgs += [{"role": "assistant", "content": f"a{i}"} for i in range(10)]
        store.get_session_by_id.return_value = session
        store.get_session_messages.return_value = msgs
        store.create_session.return_value = new_session

        router = AsyncMock()
        router.build_candidates = MagicMock(return_value=[("p1", "m1")])
        provider = AsyncMock()
        provider.generate = AsyncMock(side_effect=RuntimeError("LLM down"))
        router.get_model_provider = MagicMock(return_value=provider)

        ok, text, new_id = await mgr.compact_session(1, router=router)

        assert ok is True
        assert "部分历史已丢弃" in text

    @pytest.mark.asyncio
    async def test_compaction_preserves_tracker_state(self, mgr, store):
        """压缩后 tracker 状态（notified_event_ids、last_context_update_at）保持连续"""
        from datetime import datetime as _dt

        session = _make_session(session_id=1)
        new_session = _make_session(session_id=2)
        msgs = [{"role": "user", "content": f"u{i}"} for i in range(10)]
        msgs += [{"role": "assistant", "content": f"a{i}"} for i in range(10)]
        store.get_session_by_id.return_value = session
        store.get_session_messages.return_value = msgs
        store.create_session.return_value = new_session

        # 预填充 tracker 状态
        ctx_time = _dt(2026, 6, 1, 10, 0, 0)
        tracker = mgr.get_tracker("u1")
        tracker["notified_event_ids"] = {1, 2, 3}
        tracker["last_context_update_at"] = ctx_time
        tracker["last_event_notification_date"] = "2026-06-01"

        ok, _, _ = await mgr.compact_session(1, router=None)

        assert ok is True
        # 压缩后 tracker 状态应保持连续
        tracker_after = mgr.get_tracker("u1")
        assert tracker_after["notified_event_ids"] == {1, 2, 3}
        assert tracker_after["last_context_update_at"] == ctx_time
        assert tracker_after["last_event_notification_date"] == "2026-06-01"


# ── Q126: SessionManager lifecycle ─────────────────────────────────────────────


class TestSessionLifecycle:
    """SessionManager 生命周期方法：delete_session / add_bypass_message / on_chat_complete / update_token_estimate"""

    @pytest.fixture
    def store(self):
        s = AsyncMock()
        s.get_active_session = AsyncMock()
        s.create_session = AsyncMock()
        s.update_session = AsyncMock()
        s.delete_session = AsyncMock()
        s.get_session_by_id = AsyncMock()
        s.get_session_messages = AsyncMock(return_value=[])
        s.add_session_messages = AsyncMock()
        return s

    @pytest.fixture
    def mgr(self, store):
        from plugins.DicePP.module.persona.chat.chat_config import ChatConfig
        config = ChatConfig()
        return SessionManager(store=store, config=config)

    # ── delete_session ───────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_delete_session_deletes_and_clears_tracker(self, mgr, store):
        """delete_session 调用 store.delete_session 并清理 tracker"""
        active = _make_session(session_id=1)
        store.get_active_session.return_value = active

        # 填充 tracker
        mgr._trackers["u1"] = {"test": "value"}

        await mgr.delete_session("u1")

        store.delete_session.assert_awaited_once_with(1)
        # tracker 应被清除
        assert "u1" not in mgr._trackers

    @pytest.mark.asyncio
    async def test_delete_session_no_active_is_noop(self, mgr, store):
        """无活跃 session 时 delete_session 安全返回"""
        store.get_active_session.return_value = None
        await mgr.delete_session("u1")
        store.delete_session.assert_not_called()

    # ── add_bypass_message ───────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_add_bypass_message_appends_to_session(self, mgr, store):
        """add_bypass_message 向 session 追加一条 user 消息"""
        await mgr.add_bypass_message(1, "旁路消息")

        store.add_session_messages.assert_awaited_once()
        args, _ = store.add_session_messages.await_args
        assert args[0] == 1  # session_id
        assert len(args[1]) == 1  # one message
        assert args[1][0].role == "user"
        assert args[1][0].content == "旁路消息"

    # ── update_token_estimate ────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_update_token_estimate(self, mgr, store):
        """update_token_estimate 更新 session 的 token_estimate"""
        await mgr.update_token_estimate(1, 5000)
        store.update_session.assert_awaited_once_with(1, token_estimate=5000)

    # ── on_chat_complete ────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_on_chat_complete_appends_and_updates(self, mgr, store):
        """on_chat_complete 追加 assistant 回复并更新 token 估算"""
        active = _make_session(session_id=1, token_budget=64000)
        store.get_active_session.return_value = active
        # 模拟 get_session_messages 返回少量消息
        store.get_session_messages.return_value = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]

        await mgr.on_chat_complete("u1", "", "assistant reply", "user msg")

        # assistant 消息被追加
        store.add_session_messages.assert_awaited_once()
        args, _ = store.add_session_messages.await_args
        assert args[1][0].role == "assistant"
        assert args[1][0].content == "assistant reply"

        # token_estimate 被更新（update_session 至少被调用一次，证明流程走到该步骤）
        assert store.update_session.call_count >= 1

    @pytest.mark.asyncio
    async def test_on_chat_complete_empty_response(self, mgr, store):
        """空 response 时 on_chat_complete 安全返回，不做任何操作"""
        await mgr.on_chat_complete("u1", "", "", "user msg")
        store.add_session_messages.assert_not_called()

    @pytest.mark.asyncio
    async def test_on_chat_complete_no_active_session(self, mgr, store):
        """无活跃 session 时 on_chat_complete 安全返回"""
        store.get_active_session.return_value = None
        await mgr.on_chat_complete("u1", "", "reply", "msg")
        store.add_session_messages.assert_not_called()
