"""SessionManager 单元测试 — get_or_create / compact_session / SessionDecision"""
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
from plugins.DicePP.module.persona.data.models import PersonaSession


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
        assert "[通知] 现在是" in note
        assert "周四" in note or "周" in note


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
        assert "[通知] 现在是" in decision.notifications[0]

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
        ok, text = await mgr.compact_session(999)
        assert ok is False
        assert text is None

    @pytest.mark.asyncio
    async def test_few_messages_deletes_session(self, mgr, store):
        """不足 6 条消息 → 直接删除 session"""
        session = _make_session(session_id=1)
        store.get_session_by_id.return_value = session
        store.get_session_messages.return_value = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]

        ok, text = await mgr.compact_session(1)

        assert ok is False
        assert text is None
        store.delete_session.assert_called_once_with(1)

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

        ok, text = await mgr.compact_session(1, router=None)

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

        ok, text = await mgr.compact_session(1, router=router)

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

        ok, text = await mgr.compact_session(1, router=router)

        assert ok is True
        assert "部分历史已丢弃" in text
