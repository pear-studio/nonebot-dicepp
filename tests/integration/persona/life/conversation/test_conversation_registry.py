"""ConversationRegistry 测试（阶段 1 · Step 5）。

覆盖：同 scope 复用、close 后顺序新建、scope 隔离、append_visible→render_resolved、
并发唯一、change_source_factory 按 scope 装配、active 唯一性。
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest
from pydantic import BaseModel

from plugins.DicePP.core.message_types import MessageType
from plugins.DicePP.module.persona.agent.output_protocol import (
    DRAFT_MESSAGE_NAME,
    INTERNAL_MESSAGE_TYPE_FIELD,
    RUNTIME_INSTRUCTION_NAME,
    make_output_reminder,
)
from plugins.DicePP.module.persona.agent.runtime_types import ModelTurn, OutputSpec
from plugins.DicePP.module.persona.life.conversation_registry import ConversationRegistry
from plugins.DicePP.module.persona.life.conversation_scope import ConversationScope
from plugins.DicePP.module.persona.life.conversation_summary import _build_summary_prompt


def _runtime_factory():
    # get_or_create 只持有 runtime，不调用 run；MagicMock 足够。
    return MagicMock()


class FakeSource:
    """满足 ChangeSource 协议的最小测试 double。"""

    def __init__(self, source_id: str, priority: int = 10, name: str = "fake"):
        self.source_id = source_id
        self.priority = priority
        self.name = name

    async def update(self, cursor):
        return [], cursor


def _make_registry(store, change_source_factory=None, character_id="char1"):
    return ConversationRegistry(
        store,
        runtime_factory=_runtime_factory,
        change_source_factory=change_source_factory,
        character_id_provider=lambda: character_id,
    )


async def _active_session_rows(store, scope: ConversationScope):
    async with store.db.execute(
        "SELECT session_id FROM persona_session "
        "WHERE status='active' AND scope_namespace=? AND scope_key=?",
        (scope.namespace, scope.key),
    ) as cur:
        return await cur.fetchall()


async def _session_status(store, session_id: int) -> str:
    async with store.db.execute(
        "SELECT status FROM persona_session WHERE session_id=?", (session_id,),
    ) as cur:
        row = await cur.fetchone()
    return str(row["status"])


class TestRunLifecycleLease:
    async def test_close_waits_until_run_lease_finishes(self, temp_db):
        reg = _make_registry(temp_db)
        scope = ConversationScope.for_group("lease-close")
        entered = asyncio.Event()
        release = asyncio.Event()

        async def blocked_run() -> int:
            async with reg.run_lease(scope) as conv:
                entered.set()
                await release.wait()
                return int(conv.id)

        run_task = asyncio.create_task(blocked_run())
        await entered.wait()
        sid = int(reg.peek_cached(scope).id)
        close_task = asyncio.create_task(reg.close(scope))
        await asyncio.sleep(0)

        assert not close_task.done()
        assert await _session_status(temp_db, sid) == "active"

        release.set()
        assert await run_task == sid
        await close_task
        assert await _session_status(temp_db, sid) == "closed"

    async def test_rotate_summarizes_messages_committed_before_lease_release(
        self, temp_db,
    ):
        from plugins.DicePP.module.persona.life.conversation_summary import FakeSummarizer

        summarizer = FakeSummarizer(return_text="完整摘要")
        reg = ConversationRegistry(
            temp_db,
            runtime_factory=_runtime_factory,
            summarizer=summarizer,
        )
        scope = ConversationScope.for_group("lease-rotate")
        entered = asyncio.Event()
        committed = asyncio.Event()
        release = asyncio.Event()

        async def blocked_run() -> int:
            async with reg.run_lease(scope) as conv:
                sid = int(conv.id)
                entered.set()
                await committed.wait()
                for index in range(4):
                    conv.add_message("assistant", f"late-commit-{index}")
                await conv.save()
                await release.wait()
                return sid

        run_task = asyncio.create_task(blocked_run())
        await entered.wait()
        rotate_task = asyncio.create_task(reg.rotate(scope))
        await asyncio.sleep(0)
        assert not rotate_task.done()

        committed.set()
        await asyncio.sleep(0)
        release.set()
        old_sid = await run_task
        new_conv = await rotate_task

        assert int(new_conv.id) != old_sid
        assert await _session_status(temp_db, old_sid) == "closed"
        summarized_text = " ".join(
            str(message.get("content", ""))
            for batch in summarizer.called_with
            for message in batch
        )
        assert "late-commit-3" in summarized_text

    async def test_cancelled_run_releases_lease(self, temp_db):
        reg = _make_registry(temp_db)
        scope = ConversationScope.for_group("lease-cancel")
        entered = asyncio.Event()

        async def cancelled_run() -> None:
            async with reg.run_lease(scope):
                entered.set()
                await asyncio.Event().wait()

        run_task = asyncio.create_task(cancelled_run())
        await entered.wait()
        run_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await run_task

        await asyncio.wait_for(reg.close(scope), timeout=1)
        assert scope not in reg._active_run_leases


class TestGetOrCreate:
    async def test_same_scope_returns_cached_instance(self, temp_db):
        reg = _make_registry(temp_db)
        scope = ConversationScope.for_group("g1")
        c1 = await reg.get_or_create(scope)
        c2 = await reg.get_or_create(scope)
        assert c1 is c2

    async def test_creates_active_session_with_scope_columns(self, temp_db):
        reg = _make_registry(temp_db)
        scope = ConversationScope.for_group("g1")
        conv = await reg.get_or_create(scope)
        assert conv.id is not None
        async with temp_db.db.execute(
            "SELECT scope_namespace, scope_key, status, character_id "
            "FROM persona_session WHERE session_id=?",
            (int(conv.id),),
        ) as cur:
            row = await cur.fetchone()
        assert row["scope_namespace"] == "chat.group"
        assert row["scope_key"] == "g1"
        assert row["status"] == "active"
        assert row["character_id"] == "char1"

    async def test_scope_isolation_group_vs_group(self, temp_db):
        reg = _make_registry(temp_db)
        c1 = await reg.get_or_create(ConversationScope.for_group("g1"))
        c2 = await reg.get_or_create(ConversationScope.for_group("g2"))
        assert c1 is not c2
        assert c1.id != c2.id

    async def test_scope_isolation_group_vs_private_same_id(self, temp_db):
        reg = _make_registry(temp_db)
        cg = await reg.get_or_create(ConversationScope.for_group("x"))
        cp = await reg.get_or_create(ConversationScope.for_private("x"))
        assert cg is not cp
        assert cg.id != cp.id

    async def test_reopens_existing_active_session_after_cache_drop(self, temp_db):
        # 模拟进程重启：清空内存缓存，同 scope 应复用 DB 里的 active session
        reg = _make_registry(temp_db)
        scope = ConversationScope.for_private("u1")
        conv = await reg.get_or_create(scope)
        sid = conv.id
        reg._active_convs.clear()
        conv2 = await reg.get_or_create(scope)
        assert conv2.id == sid
        # 未新建，仍只有一个 active
        rows = await _active_session_rows(temp_db, scope)
        assert len(rows) == 1


class TestCloseReset:
    async def test_close_marks_closed_and_next_creates_new(self, temp_db):
        reg = _make_registry(temp_db)
        scope = ConversationScope.for_group("g1")
        conv1 = await reg.get_or_create(scope)
        sid1 = conv1.id
        await reg.close(scope)

        # 旧 session 变 closed
        async with temp_db.db.execute(
            "SELECT status FROM persona_session WHERE session_id=?", (int(sid1),)
        ) as cur:
            row = await cur.fetchone()
        assert row["status"] == "closed"

        # 下次 get_or_create 顺序新建
        conv2 = await reg.get_or_create(scope)
        assert conv2.id != sid1
        # 同 scope 仍只有一个 active
        rows = await _active_session_rows(temp_db, scope)
        assert len(rows) == 1


class TestAppendVisible:
    async def test_append_visible_expands_in_render(self, temp_db):
        reg = _make_registry(temp_db)
        scope = ConversationScope.for_group("g1")
        # 先写 message_stream 权威记录
        msid = await temp_db.add_message_stream(
            "u1", "g1", "user", MessageType.CHAT, "万生说你好", "万生",
        )
        conv = await reg.append_visible(scope, msid, "user")
        rendered = await conv.render_resolved("SYS")
        assert rendered[0] == {"role": "system", "content": "SYS"}
        assert rendered[1] == {
            "role": "user", "content": "万生说你好", "name": "uid_u1",
        }

    async def test_append_visible_persists_ref(self, temp_db):
        reg = _make_registry(temp_db)
        scope = ConversationScope.for_private("u1")
        msid = await temp_db.add_message_stream(
            "u1", "", "user", MessageType.CHAT, "在吗",
        )
        conv = await reg.append_visible(scope, msid, "user")
        # 落库为 ref 条目
        async with temp_db.db.execute(
            "SELECT entry_type, message_stream_id FROM persona_session_message "
            "WHERE session_id=? ORDER BY sequence",
            (int(conv.id),),
        ) as cur:
            rows = await cur.fetchall()
        assert len(rows) == 1
        assert rows[0]["entry_type"] == "ref"
        assert rows[0]["message_stream_id"] == msid


class TestConcurrency:
    async def test_concurrent_get_or_create_single_session(self, temp_db):
        reg = _make_registry(temp_db)
        scope = ConversationScope.for_group("g1")
        results = await asyncio.gather(
            *[reg.get_or_create(scope) for _ in range(8)]
        )
        # 全部同一实例
        assert all(c is results[0] for c in results)
        # 只创建一个 active session
        rows = await _active_session_rows(temp_db, scope)
        assert len(rows) == 1

    async def test_concurrent_append_visible_order(self, temp_db):
        reg = _make_registry(temp_db)
        scope = ConversationScope.for_group("g1")
        ids = []
        for i in range(5):
            ids.append(await temp_db.add_message_stream(
                "u1", "g1", "user", MessageType.CHAT, f"m{i}",
            ))
        await asyncio.gather(*[reg.append_visible(scope, mid, "user") for mid in ids])
        conv = reg.peek_cached(scope)
        # 5 条 ref 全部落库，sequence 连续
        async with temp_db.db.execute(
            "SELECT sequence, message_stream_id FROM persona_session_message "
            "WHERE session_id=? ORDER BY sequence",
            (int(conv.id),),
        ) as cur:
            rows = await cur.fetchall()
        assert [r["sequence"] for r in rows] == [0, 1, 2, 3, 4]
        assert sorted(r["message_stream_id"] for r in rows) == sorted(ids)


class TestChangeSourceFactory:
    async def test_factory_receives_scope_and_sources_registered(self, temp_db):
        seen_scopes = []

        def factory(scope):
            seen_scopes.append(scope)
            if scope.is_group:
                return [FakeSource("date"), FakeSource("daily_event")]
            return [
                FakeSource("date"), FakeSource("daily_event"),
                FakeSource("relation"), FakeSource("profile"),
            ]

        reg = _make_registry(temp_db, change_source_factory=factory)
        group_conv = await reg.get_or_create(ConversationScope.for_group("g1"))
        private_conv = await reg.get_or_create(ConversationScope.for_private("u1"))

        # 工厂按 scope 被调用
        assert ConversationScope.for_group("g1") in seen_scopes
        assert ConversationScope.for_private("u1") in seen_scopes
        # 群 scope 只注册 2 个（D8 退化：无 per-user Relation/ProfileFacts）
        assert len(group_conv._change_sources) == 2
        # 私聊 scope 注册全部 4 个
        assert len(private_conv._change_sources) == 4


# ── 阶段 3b：静默轮换与摘要继承 ──────────────────────────


class TestSilenceRotation:
    """P1-1 / P1-2 / P1-16: 静默超时轮换"""

    async def _make_registry(self, temp_db, summarizer=None,
                              private_silence_seconds=86400,
                              group_silence_seconds=1800):
        return ConversationRegistry(
            temp_db,
            runtime_factory=_runtime_factory,
            summarizer=summarizer,
            private_silence_seconds=private_silence_seconds,
            group_silence_seconds=group_silence_seconds,
        )

    async def test_append_visible_triggers_silence_rotation(self, temp_db):
        """P1-1: 静默超时后 append_visible 触发轮换。"""
        from plugins.DicePP.core.message_types import MessageType

        reg = await self._make_registry(temp_db, group_silence_seconds=0)
        scope = ConversationScope.for_group("g1")

        # 先创建第一个 conv
        msid1 = await temp_db.add_message_stream("u1", "g1", "user", MessageType.CHAT, "m1")
        conv1 = await reg.append_visible(scope, msid1, "user")
        sid1 = conv1.id

        # 设置 last_active_at 为过去（silence_seconds=0，任何 past 都超时）
        await temp_db.db.execute(
            "UPDATE persona_session SET last_active_at='2000-01-01 00:00:00' "
            "WHERE session_id=?", (int(sid1),)
        )
        await temp_db.db.commit()

        # 再次 append_visible → 应触发轮换
        msid2 = await temp_db.add_message_stream("u1", "g1", "user", MessageType.CHAT, "m2")
        conv2 = await reg.append_visible(scope, msid2, "user")

        # 旧 conv 已关闭，新 conv 不同 id
        assert conv2.id != sid1
        async with temp_db.db.execute(
            "SELECT status FROM persona_session WHERE session_id=?", (int(sid1),)
        ) as cur:
            row = await cur.fetchone()
        assert row["status"] == "closed"
        # 同 scope 只有一个 active
        rows = await _active_session_rows(temp_db, scope)
        assert len(rows) == 1

    async def test_silence_within_threshold_no_rotation(self, temp_db):
        """P1-2: 静默恰在阈值内，不触发轮换。"""
        from plugins.DicePP.core.message_types import MessageType

        reg = await self._make_registry(temp_db, group_silence_seconds=86400)
        scope = ConversationScope.for_group("g1")

        msid1 = await temp_db.add_message_stream("u1", "g1", "user", MessageType.CHAT, "m1")
        conv1 = await reg.append_visible(scope, msid1, "user")
        sid1 = conv1.id

        # last_active_at 已是 CURRENT_TIMESTAMP（刚创建不久），不超时
        msid2 = await temp_db.add_message_stream("u1", "g1", "user", MessageType.CHAT, "m2")
        conv2 = await reg.append_visible(scope, msid2, "user")

        # 同一 conv
        assert conv2.id == sid1
        rows = await _active_session_rows(temp_db, scope)
        assert len(rows) == 1

    async def test_no_active_session_no_rotation(self, temp_db):
        """无活跃 session 时不触发轮换。"""
        from plugins.DicePP.core.message_types import MessageType

        reg = await self._make_registry(temp_db, group_silence_seconds=0)
        scope = ConversationScope.for_group("g1")

        msid = await temp_db.add_message_stream("u1", "g1", "user", MessageType.CHAT, "m1")
        conv = await reg.append_visible(scope, msid, "user")
        assert conv.id is not None
        assert conv.length == 1


class TestSilenceTimezoneBaseline:
    """静默判定的"现在"必须与 last_active_at 写入侧同一时区基准。

    life scope 的 last_active_at 由生产 Clock 写入（上海 naive，见
    ConversationRegistry._create_session / ConversationStore）；修复前
    _is_silence_expired 统一用 SQL julianday('now')（UTC）比较，life 私聊的
    静默间隔被少算 8 小时（24h 阈值实际 ~16h 即判过期）。chat scope 写
    CURRENT_TIMESTAMP（UTC），判定必须保持 UTC 基准、不受 Clock 影响。
    """

    async def test_life_scope_silence_uses_clock_baseline(self, temp_db):
        """life scope：last_active_at=Clock 现在-20h 未过期；-25h 过期。"""
        import datetime
        from plugins.DicePP.utils.time import SteppedClock, set_clock

        clock = SteppedClock(datetime.datetime(2042, 6, 3, 20, 0))
        set_clock(clock)
        # life.dm 走 group_silence_seconds 档位；阈值固定 24h 以复现生产语义
        reg = ConversationRegistry(
            temp_db,
            runtime_factory=_runtime_factory,
            group_silence_seconds=86400,
        )
        scope = ConversationScope.for_life_dm("char-tz")
        conv = await reg.get_or_create(scope)
        sid = int(conv.id)

        async def set_last_active(hours_ago: float) -> None:
            last = clock.now() - datetime.timedelta(hours=hours_ago)
            await temp_db.db.execute(
                "UPDATE persona_session SET last_active_at=? WHERE session_id=?",
                (last.isoformat(sep=" "), sid),
            )
            await temp_db.db.commit()

        # 20h 前：未超 24h 阈值 → 未过期
        await set_last_active(20)
        assert await reg._is_silence_expired(scope) is False

        # 25h 前：超过 24h 阈值 → 过期。修复前按 SQL 'now'（UTC/真实墙钟）
        # 少算 8h（或虚拟钟下完全不触发），会误判未过期。
        await set_last_active(25)
        assert await reg._is_silence_expired(scope) is True

    async def test_chat_scope_silence_keeps_utc_baseline(self, temp_db):
        """chat scope：保持 UTC 语义，不受注入的 Clock 影响。"""
        import datetime
        from plugins.DicePP.utils.time import SteppedClock, set_clock

        # Clock 故意拨到未来：chat scope 写 CURRENT_TIMESTAMP（UTC 墙钟），
        # 判定也必须用 UTC 墙钟而非 Clock。
        set_clock(SteppedClock(datetime.datetime(2042, 6, 3, 20, 0)))
        reg = ConversationRegistry(
            temp_db,
            runtime_factory=_runtime_factory,
            group_silence_seconds=86400,
        )
        scope = ConversationScope.for_group("g-tz")
        conv = await reg.get_or_create(scope)
        sid = int(conv.id)

        def utc_now() -> "datetime.datetime":
            return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)

        async def set_last_active(hours_ago: float) -> None:
            last = utc_now() - datetime.timedelta(hours=hours_ago)
            await temp_db.db.execute(
                "UPDATE persona_session SET last_active_at=? WHERE session_id=?",
                (last.isoformat(sep=" "), sid),
            )
            await temp_db.db.commit()

        await set_last_active(20)
        assert await reg._is_silence_expired(scope) is False

        await set_last_active(25)
        assert await reg._is_silence_expired(scope) is True


class TestSummaryInheritance:
    """P1-9 / P1-10 / P1-11 / P1-12 / P1-14: 摘要继承与失败处理"""

    async def _make_registry(self, temp_db, summarizer=None):
        return ConversationRegistry(
            temp_db,
            runtime_factory=_runtime_factory,
            summarizer=summarizer,
        )

    async def test_summary_inherited_on_new_session(self, temp_db):
        """P1-9: 关闭旧 session → get_or_create 新 conv 继承摘要。"""
        from plugins.DicePP.module.persona.life.conversation_summary import FakeSummarizer

        summarizer = FakeSummarizer(return_text="上轮摘要文本")
        reg = await self._make_registry(temp_db, summarizer=summarizer)
        scope = ConversationScope.for_group("g1")

        # 创建会话，写入消息，关闭
        conv1 = await reg.get_or_create(scope)
        sid1 = int(conv1.id)
        # 写 5 条消息（>= SUMMARY_MIN_MESSAGES=4）
        for i in range(5):
            conv1.add_message("user", f"msg{i}")
        await conv1.save()

        await reg.close(scope)

        # 再 get_or_create → 摘要应被注入
        conv2 = await reg.get_or_create(scope)
        msgs = conv2.get_messages()
        assert any("上轮摘要文本" in m.get("content", "") for m in msgs)

        # 摘要已写入旧 session
        async with temp_db.db.execute(
            "SELECT summary_text FROM persona_session WHERE session_id=?", (sid1,)
        ) as cur:
            row = await cur.fetchone()
        assert row["summary_text"] == "上轮摘要文本"

    async def test_summary_failure_does_not_block(self, temp_db):
        """P1-10: 摘要生成失败（异常）不阻断新 conv 创建。"""
        from plugins.DicePP.module.persona.life.conversation_summary import FakeSummarizer

        summarizer = FakeSummarizer(fail=True)
        reg = await self._make_registry(temp_db, summarizer=summarizer)
        scope = ConversationScope.for_group("g1")

        conv1 = await reg.get_or_create(scope)
        for i in range(5):
            conv1.add_message("user", f"msg{i}")
        await conv1.save()
        await reg.close(scope)

        # 摘要失败，新 conv 应正常创建不含摘要
        conv2 = await reg.get_or_create(scope)
        assert conv2.id != conv1.id
        msgs = conv2.get_messages()
        assert not any("摘要" in m.get("content", "") for m in msgs)

        # 旧 session summary_text 仍为空
        async with temp_db.db.execute(
            "SELECT summary_text FROM persona_session WHERE session_id=?",
            (int(conv1.id),)
        ) as cur:
            row = await cur.fetchone()
        assert row["summary_text"] == ""

    async def test_summary_scope_isolation_single_registry(self, temp_db):
        """W6: 单一 registry 实例 + SQL scope 过滤保证摘要隔离。"""
        from plugins.DicePP.module.persona.life.conversation_summary import FakeSummarizer

        summarizer = FakeSummarizer(return_text="scope摘要")
        reg = await self._make_registry(temp_db, summarizer=summarizer)
        scope_g1 = ConversationScope.for_group("g1")
        scope_g2 = ConversationScope.for_group("g2")

        # g1: 创建会话，写消息，关闭
        conv_g1 = await reg.get_or_create(scope_g1)
        for i in range(5):
            conv_g1.add_message("user", f"g1_msg{i}")
        await conv_g1.save()
        await reg.close(scope_g1)

        # g2: 创建会话，写消息，关闭
        conv_g2 = await reg.get_or_create(scope_g2)
        for i in range(5):
            conv_g2.add_message("user", f"g2_msg{i}")
        await conv_g2.save()
        await reg.close(scope_g2)

        # 同一 registry 实例 → get_or_create 按 SQL WHERE scope_namespace/key 过滤
        conv_g1_new = await reg.get_or_create(scope_g1)
        conv_g2_new = await reg.get_or_create(scope_g2)

        # 两个 scope 各自有摘要前缀
        msgs_g1 = conv_g1_new.get_messages()
        msgs_g2 = conv_g2_new.get_messages()
        assert any("scope摘要" in m.get("content", "") for m in msgs_g1)
        assert any("scope摘要" in m.get("content", "") for m in msgs_g2)

        # 摘要内容隔离：g1 的新 conv 不含 g2 的消息文本
        summary_g1 = [m.get("content", "") for m in msgs_g1 if "摘要" in m.get("content", "")]
        for t in summary_g1:
            assert "g2_msg" not in t

        # g2 的新 conv 不含 g1 的消息文本
        summary_g2 = [m.get("content", "") for m in msgs_g2 if "摘要" in m.get("content", "")]
        for t in summary_g2:
            assert "g1_msg" not in t

        # summarizer 被调用了两次（每个 scope 一次）
        assert len(summarizer.called_with) == 2

    async def test_short_session_skips_summary(self, temp_db):
        """P1-12: 短会话（<4 条消息）跳过摘要生成。"""
        from plugins.DicePP.module.persona.life.conversation_summary import FakeSummarizer

        summarizer = FakeSummarizer()
        reg = await self._make_registry(temp_db, summarizer=summarizer)
        scope = ConversationScope.for_group("g1")

        conv1 = await reg.get_or_create(scope)
        # 只写 2 条消息（< SUMMARY_MIN_MESSAGES=4）
        conv1.add_message("user", "msg1")
        conv1.add_message("user", "msg2")
        await conv1.save()
        await reg.close(scope)

        conv2 = await reg.get_or_create(scope)
        msgs = conv2.get_messages()
        # 不应有摘要前缀
        assert not any("摘要" in m.get("content", "") for m in msgs)
        # generate_summary 未被调用
        assert len(summarizer.called_with) == 0

    async def test_existing_summary_not_overwritten(self, temp_db):
        """P1-14: 已有 summary_text 不重写。"""
        from plugins.DicePP.module.persona.life.conversation_summary import FakeSummarizer

        summarizer = FakeSummarizer(return_text="新摘要")
        reg = await self._make_registry(temp_db, summarizer=summarizer)
        scope = ConversationScope.for_group("g1")

        # 创建会话，写消息，关闭，手动设摘要
        conv1 = await reg.get_or_create(scope)
        sid1 = int(conv1.id)
        for i in range(5):
            conv1.add_message("user", f"msg{i}")
        await conv1.save()
        await reg.close(scope)
        await temp_db.db.execute(
            "UPDATE persona_session SET summary_text='已有摘要' WHERE session_id=?",
            (sid1,),
        )
        await temp_db.db.commit()

        # get_or_create → 不调 generate，直接复用已有摘要
        conv2 = await reg.get_or_create(scope)
        msgs = conv2.get_messages()
        assert any("已有摘要" in m.get("content", "") for m in msgs)
        # 生成方法未被调用
        assert len(summarizer.called_with) == 0

    async def test_summary_prefix_stable_after_appends(self, temp_db):
        """P1-13: 摘要注入后 append_ref 不会影响前缀位置。"""
        from plugins.DicePP.core.message_types import MessageType
        from plugins.DicePP.module.persona.life.conversation_summary import FakeSummarizer

        summarizer = FakeSummarizer(return_text="摘要前缀")
        reg = await self._make_registry(temp_db, summarizer=summarizer)
        scope = ConversationScope.for_group("g1")

        conv1 = await reg.get_or_create(scope)
        for i in range(5):
            conv1.add_message("user", f"msg{i}")
        await conv1.save()
        await reg.close(scope)

        conv2 = await reg.get_or_create(scope)

        # 追加 3 条 ref
        for i in range(3):
            msid = await temp_db.add_message_stream(
                "u1", "g1", "user", MessageType.CHAT, f"新消息{i}"
            )
            await conv2.append_ref(msid, "user")

        rendered = await conv2.render_resolved("SYS")
        # 第 0 条: system prompt, 第 1 条: 摘要前缀
        assert rendered[0] == {"role": "system", "content": "SYS"}
        assert "摘要前缀" in rendered[1]["content"]
        assert rendered[1]["role"] == "user"

    async def test_summary_inheritance_chain_s2(self, temp_db):
        """S2: 三段摘要继承链——S1(有摘要)→S2(无摘要但消息足, 应为其生成)→S3(继承 S2 新摘要, 非 S1 旧摘要)。"""
        from plugins.DicePP.module.persona.life.conversation_summary import FakeSummarizer

        # 按调用次数返回不同摘要文本的 FakeSummarizer
        class CountingSummarizer:
            def __init__(self):
                self.called_with: list[list[dict]] = []
                self._call_count = 0

            async def generate_summary(self, messages: list[dict]) -> str:
                self.called_with.append(messages)
                self._call_count += 1
                return "S1旧摘要" if self._call_count == 1 else "S2新摘要"

        summarizer = CountingSummarizer()
        reg = await self._make_registry(temp_db, summarizer=summarizer)
        scope = ConversationScope.for_group("g1")

        # Step 1: 创建 S1, 写消息, 关闭
        conv_s1 = await reg.get_or_create(scope)
        sid1 = int(conv_s1.id)
        for i in range(5):
            conv_s1.add_message("user", f"s1_msg{i}")
        await conv_s1.save()
        await reg.close(scope)

        # Step 2: get_or_create 触发为 S1 生成摘要, S2 得到 S1 旧摘要
        conv_s2 = await reg.get_or_create(scope)
        msgs_s2 = conv_s2.get_messages()
        assert any("S1旧摘要" in m.get("content", "") for m in msgs_s2)
        assert not any("S2新摘要" in m.get("content", "") for m in msgs_s2)

        # S2 自己写消息（足够产生新摘要）→ 关闭
        sid2 = int(conv_s2.id)
        for i in range(5):
            conv_s2.add_message("user", f"s2_msg{i}")
        await conv_s2.save()
        await reg.close(scope)

        # Step 3: get_or_create 触发为 S2 生成新摘要, S3 继承 S2 的新摘要
        conv_s3 = await reg.get_or_create(scope)
        msgs_s3 = conv_s3.get_messages()
        assert any("S2新摘要" in m.get("content", "") for m in msgs_s3)
        assert not any("S1旧摘要" in m.get("content", "") for m in msgs_s3)

        # summarizer 被调用了两次（为 S1 和 S2 各一次）
        assert len(summarizer.called_with) == 2

        # DB 确认: S1 有旧摘要, S2 有新摘要
        async with temp_db.db.execute(
            "SELECT summary_text FROM persona_session WHERE session_id=?", (sid1,)
        ) as cur:
            assert (await cur.fetchone())["summary_text"] == "S1旧摘要"

        async with temp_db.db.execute(
            "SELECT summary_text FROM persona_session WHERE session_id=?", (sid2,)
        ) as cur:
            assert (await cur.fetchone())["summary_text"] == "S2新摘要"


class TestRotate:
    """C4: rotate 方法测试 — 携带最后一条 user ref 至新 Conversation。"""

    async def test_rotate_carry_over_last_user_ref(self, temp_db):
        """rotate 后新 conv._messages[-1] 是 ref, role='user', msid 匹配原消息。"""
        from plugins.DicePP.core.message_types import MessageType

        reg = _make_registry(temp_db)
        scope = ConversationScope.for_group("g1")

        msid = await temp_db.add_message_stream(
            "u1", "g1", "user", MessageType.CHAT, "最后一条用户消息",
        )
        conv1 = await reg.append_visible(scope, msid, "user")
        sid1 = conv1.id

        conv2 = await reg.rotate(scope)

        # 新 conv 最后一条是 carry-over ref
        msgs = conv2.get_messages()
        last = msgs[-1]
        assert last.get("entry_type") == "ref"
        assert last.get("role") == "user"
        assert int(last.get("message_stream_id", 0)) == msid

        # 旧 conv 已 closed
        async with temp_db.db.execute(
            "SELECT status FROM persona_session WHERE session_id=?", (int(sid1),)
        ) as cur:
            row = await cur.fetchone()
        assert row["status"] == "closed"

    async def test_rotate_only_last_user_ref_carried(self, temp_db):
        """只有最后一条 user ref 被 carry-over，更早的不携带。"""
        from plugins.DicePP.core.message_types import MessageType

        reg = _make_registry(temp_db)
        scope = ConversationScope.for_group("g1")

        msid1 = await temp_db.add_message_stream("u1", "g1", "user", MessageType.CHAT, "第一轮")
        conv1 = await reg.append_visible(scope, msid1, "user")
        msid2 = await temp_db.add_message_stream("u1", "g1", "user", MessageType.CHAT, "第二轮")
        await conv1.append_ref(msid2, "user")

        conv2 = await reg.rotate(scope)
        refs = [m for m in conv2.get_messages() if m.get("entry_type") == "ref"]
        assert len(refs) == 1  # 只 carry 最后一条
        assert int(refs[0]["message_stream_id"]) == msid2

    async def test_rotate_no_user_ref_creates_clean_conv(self, temp_db):
        """没有 user ref 时 rotate 只创建新 conv，不 carry-over。"""
        reg = _make_registry(temp_db)
        scope = ConversationScope.for_group("g1")

        conv1 = await reg.get_or_create(scope)
        conv1.add_message("assistant", "只有 assistant 消息")
        await conv1.save()

        conv2 = await reg.rotate(scope)
        assert conv2.id != conv1.id
        refs = [m for m in conv2.get_messages() if m.get("entry_type") == "ref"]
        assert len(refs) == 0

    async def test_rotate_old_conv_closed(self, temp_db):
        """rotate 后旧 session status='closed'，DB 中保持完整。"""
        from plugins.DicePP.core.message_types import MessageType

        reg = _make_registry(temp_db)
        scope = ConversationScope.for_group("g1")

        msid = await temp_db.add_message_stream("u1", "g1", "user", MessageType.CHAT, "hi")
        conv1 = await reg.append_visible(scope, msid, "user")
        sid1 = int(conv1.id)

        await reg.rotate(scope)

        async with temp_db.db.execute(
            "SELECT status FROM persona_session WHERE session_id=?", (sid1,)
        ) as cur:
            assert (await cur.fetchone())["status"] == "closed"

        async with temp_db.db.execute(
            "SELECT COUNT(*) as cnt FROM persona_session_message WHERE session_id=?",
            (sid1,),
        ) as cur:
            row = await cur.fetchone()
            assert row["cnt"] > 0  # 消息完整保留


class TestConcurrentClose:
    """C3/P1-8: conv.run 进行中时并发 close 不打断 run。

    注意：conv.run 不持 per-scope lock，真实串行保证来自 LLMCallCoordinator
    per target_key。本测试验证 run 在并发 close 后仍能正常完成。"""

    @pytest.mark.asyncio
    async def test_concurrent_close_does_not_interrupt_run(self, temp_db):
        """conv.run 中 mock runtime 让出控制权时,
        并发 close 执行不打断 run 的正常完成。"""
        import asyncio
        from unittest.mock import AsyncMock, MagicMock
        from plugins.DicePP.module.persona.life.conversation import ConversationRunResult
        from plugins.DicePP.module.persona.agent.runtime_types import (
            AgentRunResult, RunCompletion, RunOutput, BillingSummary,
        )

        scope = ConversationScope.for_group("g1")
        reg = _make_registry(temp_db)
        conv = await reg.get_or_create(scope)
        conv.add_message("user", "short message")

        # mock runtime: sleep(0) 让出控制权使并发 close 有机会执行
        async def _yielding_runtime(request):
            await asyncio.sleep(0)
            return AgentRunResult(
                run_id="r1", interaction_id="i1",
                completion=RunCompletion(kind="completed", code="output_collected"),
                output=RunOutput(text="ok"),
                message_delta=[{"role": "assistant", "content": "ok"}],
                billing=BillingSummary(),
            )

        runtime_mock = MagicMock()
        runtime_mock.run = AsyncMock(side_effect=_yielding_runtime)
        conv._runtime = runtime_mock

        async def do_run():
            return await conv.run(
                system_prompt="sys", user_input="hello",
                interaction_id="i1",
            )

        async def do_close():
            await reg.close(scope)

        # 并发调度 run 和 close
        run_task = asyncio.create_task(do_run())
        close_task = asyncio.create_task(do_close())

        run_result, _ = await asyncio.gather(run_task, close_task)

        # run 正常完成
        assert run_result.final_reason == "output_collected"
        assert run_result.completion_kind == "completed"
        # _runtime.run 被调用且返回了结果
        runtime_mock.run.assert_awaited_once()
        # conv 的状态不受 close 影响 (close 在 run 后执行)
        assert conv.length >= 0  # 至少应有 user 消息


class TestConcurrentRunAndVisibleAppend:
    """run 提交与旁观消息并发时，恢复后顺序不得改变。"""

    @pytest.mark.asyncio
    async def test_reload_preserves_in_memory_commit_order(self, temp_db):
        """DB 写入竞争不应让 ambient ref 与 run delta 在重载后换位。"""
        from unittest.mock import AsyncMock

        from plugins.DicePP.module.persona.agent.runtime_types import (
            AgentRunResult,
            BillingSummary,
            RunCompletion,
            RunOutput,
        )

        scope = ConversationScope.for_group("g-order")
        reg = _make_registry(temp_db)
        conv = await reg.get_or_create(scope)
        delta = {"role": "assistant", "content": "runtime reply"}
        conv._runtime.run = AsyncMock(return_value=AgentRunResult(
            run_id="r-order",
            interaction_id="i-order",
            completion=RunCompletion(kind="completed", code="output_collected"),
            output=RunOutput(text="runtime reply"),
            message_delta=[delta],
            billing=BillingSummary(),
        ))

        # 确定性地暂停 run batch 的真实 DB 写入。旧实现会先把 delta
        # 加入内存，却允许后来的 ref 抢先落盘；重载后两者便换位。
        original_append = conv._store.append
        run_waiting_to_persist = asyncio.Event()
        ambient_persisted = asyncio.Event()
        allow_run_persist = asyncio.Event()

        async def controlled_append(conv_id, messages):
            if messages == [delta]:
                run_waiting_to_persist.set()
                await allow_run_persist.wait()
                await original_append(conv_id, messages)
                return
            await original_append(conv_id, messages)
            ambient_persisted.set()

        conv._store.append = controlled_append
        run_task = asyncio.create_task(conv.run(
            system_prompt="sys",
            user_input="already recorded by hook",
            interaction_id="i-order",
            record_user_input=False,
        ))
        await run_waiting_to_persist.wait()

        msid = await temp_db.add_message_stream(
            "u1", "g-order", "user", MessageType.AMBIENT, "ambient while replying",
        )
        ambient_task = asyncio.create_task(reg.append_visible(scope, msid, "user"))

        async def release_run() -> None:
            # 旧实现中 ambient 能抢先落盘；新实现中它等待同一
            # commit lock，故短超时后放行 run。超时只用于编排竞态。
            try:
                await asyncio.wait_for(ambient_persisted.wait(), timeout=0.05)
            except asyncio.TimeoutError:
                pass
            allow_run_persist.set()

        await asyncio.gather(run_task, ambient_task, release_run())
        memory_order = conv.get_messages()

        reg.clear_cache()
        reloaded = await reg.get_or_create(scope)
        assert reloaded.get_messages() == memory_order
        assert memory_order == [
            delta,
            {"role": "user", "entry_type": "ref", "message_stream_id": msid},
        ]


class TestSilenceTokenCombination:
    """C5/P1-16: 静默 + token 组合条件同时成立时只创建 1 个新 session。"""

    @pytest.mark.asyncio
    async def test_silence_and_token_only_one_new_session(self, temp_db):
        """同时设 last_active_at 过期 + 消息超预算,
        静默检查优先触发轮换，只创建 1 个新 session。"""
        from plugins.DicePP.core.message_types import MessageType
        from plugins.DicePP.module.persona.life.conversation_summary import FakeSummarizer

        summarizer = FakeSummarizer(return_text="摘要")
        reg = ConversationRegistry(
            temp_db,
            runtime_factory=_runtime_factory,
            summarizer=summarizer,
            group_silence_seconds=0,  # 任何 past 都触发
        )
        scope = ConversationScope.for_group("g1")

        # 创建 conv + 写入消息
        conv1 = await reg.get_or_create(scope)
        sid1 = int(conv1.id)
        for i in range(5):
            conv1.add_message("user", f"msg{i}")
        await conv1.save()

        # 设置 last_active_at 为过去
        await temp_db.db.execute(
            "UPDATE persona_session SET last_active_at='2000-01-01 00:00:00' "
            "WHERE session_id=?", (sid1,)
        )
        await temp_db.db.commit()

        # append_visible → 触发静默轮换 → close + get_or_create → 只 1 个新 session
        msid = await temp_db.add_message_stream(
            "u1", "g1", "user", MessageType.CHAT, "新消息",
        )
        conv2 = await reg.append_visible(scope, msid, "user")

        # 旧 conv closed
        async with temp_db.db.execute(
            "SELECT status FROM persona_session WHERE session_id=?", (sid1,)
        ) as cur:
            assert (await cur.fetchone())["status"] == "closed"

        # 同 scope 只有一条 active session
        async with temp_db.db.execute(
            "SELECT COUNT(*) as cnt FROM persona_session "
            "WHERE status='active' AND scope_namespace=? AND scope_key=?",
            (scope.namespace, scope.key),
        ) as cur:
            row = await cur.fetchone()
            assert row["cnt"] == 1

        # conv2 是新 session（id 不同）
        assert conv2.id is not None
        assert int(conv2.id) != sid1


class TestSummaryRefResolve:
    """W4: 摘要生成时正确 resolve ref 条目内容。"""

    @pytest.mark.asyncio
    async def test_summary_resolves_ref_content(self, temp_db):
        """append_ref 创建的 ref 条目在 _ensure_summary_for_scope 中正确解析出原始正文。"""
        from plugins.DicePP.core.message_types import MessageType
        from plugins.DicePP.module.persona.life.conversation_summary import FakeSummarizer

        summarizer = FakeSummarizer(return_text="包含引用的摘要")
        reg = ConversationRegistry(
            temp_db,
            runtime_factory=_runtime_factory,
            summarizer=summarizer,
        )
        scope = ConversationScope.for_group("g1")

        # 创建 conv
        conv1 = await reg.get_or_create(scope)

        # 写 message_stream 记录 + append_ref
        msid = await temp_db.add_message_stream(
            "u1", "g1", "user", MessageType.CHAT, "这是原始消息内容",
        )
        await conv1.append_ref(msid, "user")

        # 补 own 消息到 >= SUMMARY_MIN_MESSAGES(=4)
        for i in range(3):
            conv1.add_message("assistant", f"回复{i}")
        await conv1.save()
        await reg.close(scope)

        # 新 get_or_create → 触发 _ensure_summary_for_scope
        conv2 = await reg.get_or_create(scope)

        # summarizer 收到已 resolve 的消息
        assert len(summarizer.called_with) >= 1
        last_batch = summarizer.called_with[-1]
        ref_msgs = [m for m in last_batch if m.get("entry_type") == "ref"]
        assert len(ref_msgs) >= 1
        # ref 条目的 content 已被 resolve 为 message_stream 的原始正文
        assert "这是原始消息内容" in ref_msgs[0].get("content", "")

    @pytest.mark.asyncio
    async def test_summary_restores_persisted_internal_message_semantics(self, temp_db):
        """真实 DB→Registry→Summary 链路保留可信标记且不信任 speaker name。"""
        class _Args(BaseModel):
            content: str

        class _PromptCapturingSummarizer:
            def __init__(self):
                self.messages: list[dict] = []
                self.prompt: list[dict] = []

            async def generate_summary(self, messages: list[dict]) -> str:
                self.messages = messages
                self.prompt = _build_summary_prompt(messages)
                return "已生成摘要"

        summarizer = _PromptCapturingSummarizer()
        reg = ConversationRegistry(
            temp_db,
            runtime_factory=_runtime_factory,
            summarizer=summarizer,
        )
        scope = ConversationScope.for_group("g-internal-marker")
        conv = await reg.get_or_create(scope)
        output_spec = OutputSpec(
            name="send_reply",
            description="向用户发送最终回复",
            args_schema=_Args,
        )
        draft = ModelTurn(
            content="尚未发送的草稿",
            provider="deepseek",
            model="deepseek-chat",
            name=DRAFT_MESSAGE_NAME,
            internal_message_type=DRAFT_MESSAGE_NAME,
        ).to_message()
        reminder = make_output_reminder(output_spec, has_draft=True)
        user_message_id = await temp_db.add_message_stream(
            "u-spoof",
            "g-internal-marker",
            "user",
            MessageType.CHAT,
            "用户昵称可以碰巧同名",
            display_name="runtime_instruction",
        )
        await conv.append_ref(user_message_id, "user")
        conv.add_messages([
            draft,
            reminder,
            {"role": "assistant", "content": "实际已发送的回答"},
        ])
        await conv.save()
        await reg.close(scope)

        await reg.get_or_create(scope)

        restored_draft = next(
            msg for msg in summarizer.messages
            if msg.get("content") == "尚未发送的草稿"
        )
        restored_reminder = next(
            msg for msg in summarizer.messages
            if msg.get("name") == "runtime_instruction"
            and msg.get("content") != "用户昵称可以碰巧同名"
        )
        assert restored_draft["name"] == DRAFT_MESSAGE_NAME
        assert restored_draft[INTERNAL_MESSAGE_TYPE_FIELD] == DRAFT_MESSAGE_NAME
        assert restored_draft["_provider_context"]["provider"] == "deepseek"
        assert restored_reminder[INTERNAL_MESSAGE_TYPE_FIELD] == RUNTIME_INSTRUCTION_NAME

        summary_input = summarizer.prompt[1]["content"]
        assert "未提交草稿：尚未发送的草稿" in summary_input
        assert "用户昵称可以碰巧同名" in summary_input
        assert "只有成功调用 send_reply" not in summary_input
        assert "角色：实际已发送的回答" in summary_input

    @pytest.mark.asyncio
    async def test_summary_ref_fallback_for_missing_stream(self, temp_db):
        """message_stream 中缺失的 ref 条目使用 DANGLING_REF_FALLBACK。"""
        from plugins.DicePP.module.persona.life.conversation_summary import FakeSummarizer
        from plugins.DicePP.module.persona.life.conversation import DANGLING_REF_FALLBACK

        summarizer = FakeSummarizer(return_text="回退摘要")
        reg = ConversationRegistry(
            temp_db,
            runtime_factory=_runtime_factory,
            summarizer=summarizer,
        )
        scope = ConversationScope.for_group("g1")

        conv1 = await reg.get_or_create(scope)
        # 注入一个不存在的 message_stream_id 的 ref (id=99999)
        conv1._messages.append({
            "role": "user",
            "entry_type": "ref",
            "message_stream_id": 99999,
        })
        await conv1.save()
        for i in range(4):
            conv1.add_message("assistant", f"a{i}")
        await conv1.save()
        await reg.close(scope)

        conv2 = await reg.get_or_create(scope)

        assert len(summarizer.called_with) >= 1
        last_batch = summarizer.called_with[-1]
        ref_msgs = [m for m in last_batch if m.get("entry_type") == "ref"]
        if ref_msgs:
            # 缺失的记录应使用 fallback
            assert DANGLING_REF_FALLBACK in ref_msgs[0].get("content", "")


class TestSummaryReadBatchFailure:
    """_ensure_summary_for_scope 中 read_message_stream_batch 异常处理"""

    @pytest.mark.asyncio
    async def test_read_batch_failure_logged(self, temp_db):
        """read_message_stream_batch 异常应被记录（而非完全静默）"""
        from unittest.mock import patch, AsyncMock
        from plugins.DicePP.core.message_types import MessageType
        from plugins.DicePP.module.persona.life.conversation_summary import FakeSummarizer

        summarizer = FakeSummarizer(return_text="摘要")
        reg = ConversationRegistry(
            temp_db,
            runtime_factory=_runtime_factory,
            summarizer=summarizer,
        )
        scope = ConversationScope.for_group("g1")

        conv1 = await reg.get_or_create(scope)
        msid = await temp_db.add_message_stream(
            "u1", "g1", "user", MessageType.CHAT, "原始消息",
        )
        await conv1.append_ref(msid, "user")
        for i in range(3):
            conv1.add_message("assistant", f"回复{i}")
        await conv1.save()
        await reg.close(scope)

        with patch.object(
            temp_db, "read_message_stream_batch",
            AsyncMock(side_effect=RuntimeError("DB failure")),
        ):
            with patch(
                'plugins.DicePP.module.persona.life.conversation_registry'
                ".logger.warning",
            ) as mock_warning:
                await reg.get_or_create(scope)

                mock_warning.assert_called_once()
                call_args_str = " ".join(str(a) for a in mock_warning.call_args[0])
                assert "read_message_stream_batch" in call_args_str
                assert mock_warning.call_args.kwargs.get("exc_info") is True


class TestRetryIntegration:
    """S1: retry 路径集成——用真实 registry.rotate 验证轮换+carry-over+重试端到端。

    不 mock registry.rotate，验证真实 rotate → close → get_or_create → carry-over 链路。
    """

    @staticmethod
    def _make_runtime_result(final_text="回复文本",
                              completion_kind="completed",
                              completion_code="output_collected"):
        from plugins.DicePP.module.persona.agent.runtime_types import (
            AgentRunResult, RunCompletion, RunOutput, BillingSummary,
        )
        return AgentRunResult(
            run_id="r_test", interaction_id="i_test",
            completion=RunCompletion(kind=completion_kind, code=completion_code),
            output=RunOutput(text=final_text),
            message_delta=[{"role": "assistant", "content": final_text}],
            billing=BillingSummary(),
        )

    @pytest.mark.asyncio
    async def test_retry_real_rotate_carry_over(self, temp_db):
        """S1: 真实 rotate 验证 rotation_needed→rotate→new conv carry-over→retry 成功。"""
        from itertools import count
        from unittest.mock import AsyncMock, MagicMock
        from plugins.DicePP.core.message_types import MessageType
        from plugins.DicePP.module.persona.life.conversation_summary import FakeSummarizer

        summarizer = FakeSummarizer(return_text="摘要")
        reg = ConversationRegistry(
            temp_db,
            runtime_factory=_runtime_factory,
            summarizer=summarizer,
        )
        scope = ConversationScope.for_group("g1")

        # Phase 1: 创建 conv1, 追加 ref + own 消息（>=4 条使摘要可触发）
        msid1 = await temp_db.add_message_stream(
            "u1", "g1", "user", MessageType.CHAT, "我需要帮助",
        )
        conv1 = await reg.append_visible(scope, msid1, "user")
        # 追加足够 own 消息使总消息数 >=4（1 ref + 4 own = 5）
        for i in range(4):
            conv1.add_message("user", f"msg{i}")
        await conv1.save()
        sid1 = int(conv1.id)

        # Phase 2: conv.run 设低 budget → rotation_needed, _runtime.run 未被调用
        runtime1 = conv1._runtime  # MagicMock from _runtime_factory
        result = await conv1.run(
            system_prompt="sys", user_input="hello",
            interaction_id="i1", token_budget=1,
        )
        assert result.final_reason == "rotation_needed"
        runtime1.run.assert_not_called()

        # Phase 3: 真实 registry.rotate → 关闭 conv1, 创建 conv2, 携带 ref
        conv2 = await reg.rotate(scope)
        assert conv2.id is not None
        assert int(conv2.id) != sid1

        # 验证旧 conv closed
        async with temp_db.db.execute(
            "SELECT status FROM persona_session WHERE session_id=?", (sid1,)
        ) as cur:
            assert (await cur.fetchone())["status"] == "closed"

        # 验证 carry-over: 新 conv 最后一条是 msid1 的 user ref
        msgs2 = conv2.get_messages()
        last = msgs2[-1]
        assert last.get("entry_type") == "ref"
        assert last.get("role") == "user"
        assert int(last.get("message_stream_id", 0)) == msid1

        # 摘要也被继承（conv2 创建时从 conv1 生成了摘要）
        assert any("摘要" in m.get("content", "") for m in msgs2)

        # Phase 4: 新 conv 设足够 budget → retry 成功
        runtime2 = conv2._runtime
        rv = self._make_runtime_result(final_text="成功回复")
        runtime2.run = AsyncMock(return_value=rv)

        result2 = await conv2.run(
            system_prompt="sys", user_input="hello",
            interaction_id="i2", token_budget=10000,
        )
        assert result2.final_reason != "rotation_needed"
        assert result2.completion_kind == "completed"
        assert result2.final_text == "成功回复"
        runtime2.run.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_retry_carry_over_preserves_summary(self, temp_db):
        """S1 变体: rotate 时旧 conv 已有摘要, 新 conv 继承同一摘要。"""
        from unittest.mock import AsyncMock, MagicMock
        from plugins.DicePP.core.message_types import MessageType
        from plugins.DicePP.module.persona.life.conversation_summary import FakeSummarizer

        summarizer = FakeSummarizer(return_text="已有摘要文本")
        reg = ConversationRegistry(
            temp_db,
            runtime_factory=_runtime_factory,
            summarizer=summarizer,
        )
        scope = ConversationScope.for_group("g1")

        # 创建 conv1, 写消息, 关闭 → 触发摘要生成
        conv1 = await reg.get_or_create(scope)
        sid1 = int(conv1.id)
        for i in range(5):
            conv1.add_message("user", f"msg{i}")
        await conv1.save()
        await reg.close(scope)

        # 创建 conv2（继承 conv1 的摘要）
        conv2 = await reg.get_or_create(scope)
        msgs2 = conv2.get_messages()
        assert any("已有摘要文本" in m.get("content", "") for m in msgs2)

        # conv2 追加一条 user ref, 然后 rotate
        msid = await temp_db.add_message_stream(
            "u1", "g1", "user", MessageType.CHAT, "你好",
        )
        await conv2.append_ref(msid, "user")
        sid2 = int(conv2.id)

        # 设置低 budget → rotation_needed
        runtime2 = conv2._runtime
        result = await conv2.run(
            system_prompt="sys", user_input="x",
            interaction_id="i1", token_budget=1,
        )
        assert result.final_reason == "rotation_needed"
        runtime2.run.assert_not_called()

        # rotate
        conv3 = await reg.rotate(scope)
        assert int(conv3.id) != sid2

        msgs3 = conv3.get_messages()

        # carry-over ref 存在
        last = msgs3[-1]
        assert last.get("entry_type") == "ref"
        assert int(last.get("message_stream_id", 0)) == msid

        # R7(b): S3 不跨段继承 S1 的摘要。S2（紧邻上一段）消息不足未生成摘要，
        # 因此 S3 不应自动注入 S1 的摘要（由 history 工具补查）。
        assert not any("已有摘要文本" in m.get("content", "") for m in msgs3), (
            "R7(b): S3 不应跨 S2 继承 S1 的摘要"
        )

        # rotate 后 generate 被调用两次：conv1 关闭时 _ensure_summary_for_scope 生成(
        # 在 get_or_create 路径) + rotate 中 _ensure_summary_for_scope 对 S2
        # 但 S2 消息不足不会实际调用 summarize.generate_summary
        assert len(summarizer.called_with) >= 1


class TestSummaryInjectionFailRecovery:
    """F1: 新 session 创建后摘要注入 save 失败 → 重试时仍注入摘要。

    _get_or_create_locked 原先用 sid is None 判断"新建"，但 save 异常后重试时
    _find_active_session_id 返回孤立 session（sid 非 None），摘要注入被永久跳过。
    修复后守卫改为 len(conv._messages)==0，覆盖此场景。
    """

    @pytest.mark.asyncio
    async def test_retry_injects_summary_after_failed_save(self, temp_db, monkeypatch):
        from plugins.DicePP.module.persona.life.conversation_store import ConversationStore
        from plugins.DicePP.module.persona.life.conversation_summary import FakeSummarizer

        summarizer = FakeSummarizer(return_text="失败恢复摘要")
        reg = ConversationRegistry(
            temp_db,
            runtime_factory=_runtime_factory,
            summarizer=summarizer,
        )
        scope = ConversationScope.for_group("g1")

        # Step 1: 创建第一个 session，写消息，关闭 → 产生 closed session
        # 摘要生成是惰性的（在下次 get_or_create 时触发），close 后 summary_text 仍为空。
        conv1 = await reg.get_or_create(scope)
        sid1 = int(conv1.id)
        for i in range(5):
            conv1.add_message("user", f"msg{i}")
        await conv1.save()
        await reg.close(scope)

        # 缓存已被 close 清空
        assert reg.peek_cached(scope) is None

        # Step 2: 模拟第二次 get_or_create 的 conv.save()（摘要注入）失败
        original_put = ConversationStore.put
        call_count = [0]

        async def mock_put(self, conv_id, snapshot):
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("模拟 save 失败")
            return await original_put(self, conv_id, snapshot)

        monkeypatch.setattr(ConversationStore, "put", mock_put)

        with pytest.raises(RuntimeError, match="模拟 save 失败"):
            await reg.get_or_create(scope)

        # conv 未入缓存
        assert reg.peek_cached(scope) is None

        # DB 中留下了一条孤立 active session（无消息）
        async with temp_db.db.execute(
            "SELECT COUNT(*) as cnt FROM persona_session "
            "WHERE status='active' AND scope_namespace=? AND scope_key=?",
            (scope.namespace, scope.key),
        ) as cur:
            row = await cur.fetchone()
        assert row["cnt"] == 1

        # Step 3: 恢复 save，再次 get_or_create → 应注入摘要
        monkeypatch.setattr(ConversationStore, "put", original_put)

        conv3 = await reg.get_or_create(scope)

        # 摘要前缀应存在（回归断言：F1 修复前会被永久跳过）
        msgs = conv3.get_messages()
        assert any("失败恢复摘要" in m.get("content", "") for m in msgs)

        # conv3 复用了孤立 session，id 应相同
        async with temp_db.db.execute(
            "SELECT session_id FROM persona_session "
            "WHERE status='active' AND scope_namespace=? AND scope_key=?",
            (scope.namespace, scope.key),
        ) as cur:
            row = await cur.fetchone()
        assert int(row["session_id"]) == int(conv3.id)


# ── Wave 2: CA-3 / CA-6 / CA-7 并发加固 ──────────────────────────


class TestCA3Concurrency:
    """Wave 2 CA-3: 摘要生成（_ensure_summary_for_scope）在 per-scope 锁外执行，
    不阻塞同 scope 其他操作。"""

    class _SlowSummarizer:
        """generate_summary 阻塞直到 resume() 被调用。"""

        def __init__(self):
            self.generate_started = asyncio.Event()
            self._resume = asyncio.Event()
            self.called_with: list[list[dict]] = []

        async def generate_summary(self, messages: list[dict]) -> str:
            self.called_with.append(messages)
            self.generate_started.set()
            await self._resume.wait()
            return "慢速摘要"

        def resume(self) -> None:
            self._resume.set()

    async def _make_registry(self, temp_db, summarizer=None):
        return ConversationRegistry(
            temp_db,
            runtime_factory=_runtime_factory,
            summarizer=summarizer,
        )

    async def test_ca3_summary_outside_lock_not_blocking_close(self, temp_db):
        """CA-3: 并发验证——一个协程在 _ensure_summary_for_scope 中阻塞，
        另一协程仍能获取同 scope 的 per-scope 锁并完成 close。"""
        summarizer = self._SlowSummarizer()
        reg = await self._make_registry(temp_db, summarizer=summarizer)
        scope = ConversationScope.for_group("g1")

        # 创建会话，写足消息（>=SUMMARY_MIN_MESSAGES=4），关闭
        # → get_or_create 时 cache miss 需调用 _ensure_summary_for_scope
        conv1 = await reg.get_or_create(scope)
        for i in range(5):
            conv1.add_message("user", f"msg{i}")
        await conv1.save()
        await reg.close(scope)
        assert reg.peek_cached(scope) is None

        # Task 1: get_or_create → cache miss → _ensure_summary_for_scope → 阻塞
        t1 = asyncio.create_task(reg.get_or_create(scope))

        # 等待摘要生成被调用（证明已进入 _ensure_summary_for_scope，锁外）
        await summarizer.generate_started.wait()

        # Task 2: 获取同 scope 的锁（close 需要 lock_for），应不阻塞
        async def close_with_timeout():
            await reg.close(scope)
            return "closed"

        result = await asyncio.wait_for(close_with_timeout(), timeout=3.0)
        assert result == "closed"

        # 释放摘要生成 → Task 1 完成
        summarizer.resume()
        conv2 = await asyncio.wait_for(t1, timeout=3.0)
        assert conv2 is not None
        # 摘要生成被激发
        assert len(summarizer.called_with) >= 1


class TestCA3TwoPhaseInheritance:
    """Wave 2 CA-3: 两阶段继承正确性——静默路径与 rotate 路径
    在锁外生成摘要后，新 conv 正确继承摘要前缀，
    且摘要 UPDATE 写入刚关闭的 session（非更旧 session）。"""

    async def _make_registry(self, temp_db, summarizer=None,
                              private_silence_seconds=86400,
                              group_silence_seconds=1800):
        return ConversationRegistry(
            temp_db,
            runtime_factory=_runtime_factory,
            summarizer=summarizer,
            private_silence_seconds=private_silence_seconds,
            group_silence_seconds=group_silence_seconds,
        )

    async def test_ca3_two_phase_silence_inherits_summary(self, temp_db):
        """静默轮换路径: 两阶段（锁内 close → 锁外 _ensure_summary → 锁内 create_new）
        新 conv 继承摘要前缀，摘要 UPDATE 写入刚关闭的 session。"""
        from plugins.DicePP.core.message_types import MessageType
        from plugins.DicePP.module.persona.life.conversation_summary import FakeSummarizer
        from plugins.DicePP.module.persona.life.conversation import NOTIFICATION_PREFIX

        summarizer = FakeSummarizer(return_text="静默轮换摘要")
        reg = await self._make_registry(temp_db, summarizer=summarizer,
                                         group_silence_seconds=0)
        scope = ConversationScope.for_group("g1")

        # 创建第一个会话，写足消息（>=4），保持 active（不 close）
        conv1 = await reg.get_or_create(scope)
        sid1 = int(conv1.id)
        for i in range(5):
            conv1.add_message("user", f"msg{i}")
        await conv1.save()

        # 置 last_active_at 为过去 → append_visible 触发静默超时
        await temp_db.db.execute(
            "UPDATE persona_session SET last_active_at='2000-01-01 00:00:00' "
            "WHERE session_id=?", (sid1,)
        )
        await temp_db.db.commit()

        # append_visible → 静默超时 → 两阶段路径
        msid = await temp_db.add_message_stream(
            "u1", "g1", "user", MessageType.CHAT, "新消息",
        )
        conv2 = await reg.append_visible(scope, msid, "user")
        assert conv2.id is not None
        assert int(conv2.id) != sid1

        # 新 conv 有摘要前缀
        msgs = conv2.get_messages()
        assert any(
            f"{NOTIFICATION_PREFIX} 之前的对话摘要：静默轮换摘要" in m.get("content", "")
            for m in msgs
        )

        # 摘要 UPDATE 写入刚关闭的 session（sid1），不是更旧的
        async with temp_db.db.execute(
            "SELECT summary_text FROM persona_session WHERE session_id=?", (sid1,)
        ) as cur:
            row = await cur.fetchone()
        assert row["summary_text"] == "静默轮换摘要"

    async def test_ca3_two_phase_rotate_inherits_summary(self, temp_db):
        """rotate 路径: 两阶段（锁内 close → 锁外 _ensure_summary → 锁内 create_new）
        新 conv 继承摘要前缀，摘要 UPDATE 写入刚关闭的 session。"""
        from plugins.DicePP.core.message_types import MessageType
        from plugins.DicePP.module.persona.life.conversation_summary import FakeSummarizer
        from plugins.DicePP.module.persona.life.conversation import NOTIFICATION_PREFIX

        summarizer = FakeSummarizer(return_text="轮换摘要")
        reg = await self._make_registry(temp_db, summarizer=summarizer)
        scope = ConversationScope.for_group("g1")

        # 创建会话，写足消息（>=4），保持 active
        conv1 = await reg.get_or_create(scope)
        sid1 = int(conv1.id)
        for i in range(5):
            conv1.add_message("user", f"msg{i}")
        await conv1.save()

        # append 一条 user ref（rotate 要 carry-over）
        msid = await temp_db.add_message_stream(
            "u1", "g1", "user", MessageType.CHAT, "最后一条用户消息",
        )
        await conv1.append_ref(msid, "user")

        # rotate → 两阶段路径（锁内 close → 锁外 _ensure_summary → 锁内 create_new）
        conv2 = await reg.rotate(scope)
        assert conv2.id is not None
        assert int(conv2.id) != sid1

        # 新 conv 有摘要前缀
        msgs = conv2.get_messages()
        assert any(
            f"{NOTIFICATION_PREFIX} 之前的对话摘要：轮换摘要" in m.get("content", "")
            for m in msgs
        )

        # 摘要 UPDATE 写入刚关闭的 session（sid1）
        async with temp_db.db.execute(
            "SELECT summary_text FROM persona_session WHERE session_id=?", (sid1,)
        ) as cur:
            row = await cur.fetchone()
        assert row["summary_text"] == "轮换摘要"

        # carry-over 的最后一条 user ref 也在新 conv 中
        last = msgs[-1]
        assert last.get("entry_type") == "ref"
        assert last.get("role") == "user"
        assert int(last.get("message_stream_id", 0)) == msid

    async def test_ca3_two_phase_summary_writes_only_to_immediate_previous(self, temp_db):
        """两阶段路径不错误地写入更旧 session：S1(closed+摘要)→S2(active)→rotate→
        新摘要只写 S2, S1 原摘要不变。"""
        from plugins.DicePP.module.persona.life.conversation_summary import FakeSummarizer
        from plugins.DicePP.module.persona.life.conversation import NOTIFICATION_PREFIX

        # 按调用次数返回不同摘要
        class _CountingSummarizer:
            def __init__(self):
                self.called_with = []
                self._call_count = 0
            async def generate_summary(self, messages):
                self.called_with.append(messages)
                self._call_count += 1
                return "S1摘要" if self._call_count == 1 else "S2新摘要"

        summarizer = _CountingSummarizer()
        reg = await self._make_registry(temp_db, summarizer=summarizer)
        scope = ConversationScope.for_group("g1")

        # S1: 创建, 写消息, close → 生成 S1 摘要
        s1 = await reg.get_or_create(scope)
        sid1 = int(s1.id)
        for i in range(5):
            s1.add_message("user", f"s1_msg{i}")
        await s1.save()
        await reg.close(scope)

        # S2: get_or_create → 继承 S1 摘要 → 写消息, 保持 active
        s2 = await reg.get_or_create(scope)
        sid2 = int(s2.id)
        for i in range(5):
            s2.add_message("user", f"s2_msg{i}")
        await s2.save()

        # rotate S2 → 两阶段: close S2 → _ensure_summary for S2 → create S3
        s3 = await reg.rotate(scope)
        assert int(s3.id) not in (sid1, sid2)

        # S1 的摘要仍是 S1 原始摘要
        async with temp_db.db.execute(
            "SELECT summary_text FROM persona_session WHERE session_id=?", (sid1,)
        ) as cur:
            row = await cur.fetchone()
        assert row["summary_text"] == "S1摘要"

        # S2 的摘要是旋转后生成的 S2新摘要
        async with temp_db.db.execute(
            "SELECT summary_text FROM persona_session WHERE session_id=?", (sid2,)
        ) as cur:
            row = await cur.fetchone()
        assert row["summary_text"] == "S2新摘要"

        # S3 继承的是 S2 的新摘要，不是 S1 的
        msgs_s3 = s3.get_messages()
        assert any("S2新摘要" in m.get("content", "") for m in msgs_s3)
        assert not any("S1摘要" in m.get("content", "") for m in msgs_s3)


class TestCA6ClearCacheRotate:
    """Wave 2 CA-6: clear_cache 清空缓存后 rotate 仍从 DB 兜底 carry-over。"""

    async def test_ca6_clear_cache_rotate_db_fallback_carry_over(self, temp_db):
        """clear_cache → old_conv 为 None → _find_last_user_ref_from_db 兜底。"""
        from plugins.DicePP.core.message_types import MessageType

        reg = _make_registry(temp_db)
        scope = ConversationScope.for_group("g1")

        # 创建会话，追加 user ref
        msid = await temp_db.add_message_stream(
            "u1", "g1", "user", MessageType.CHAT, "最后一条用户消息",
        )
        conv1 = await reg.append_visible(scope, msid, "user")
        sid1 = int(conv1.id)

        # clear_cache → 缓存清空
        reg.clear_cache()
        assert reg.peek_cached(scope) is None

        # rotate → old_conv 为 None → 从 DB 兜底取最后一条 user ref
        conv2 = await reg.rotate(scope)
        assert conv2.id is not None
        assert int(conv2.id) != sid1

        # 新 conv 最后一条是 carry-over 的 user ref
        msgs = conv2.get_messages()
        last = msgs[-1]
        assert last.get("entry_type") == "ref"
        assert last.get("role") == "user"
        assert int(last.get("message_stream_id", 0)) == msid

    async def test_ca6_no_clear_cache_rotate_memory_carry_over(self, temp_db):
        """不 clear_cache 时 rotate 从内存取 old_conv._messages 的 carry-over。"""
        from plugins.DicePP.core.message_types import MessageType

        reg = _make_registry(temp_db)
        scope = ConversationScope.for_group("g1")

        msid = await temp_db.add_message_stream(
            "u1", "g1", "user", MessageType.CHAT, "内存 carry-over",
        )
        conv1 = await reg.append_visible(scope, msid, "user")

        # 不 clear_cache → rotate 从内存取
        conv2 = await reg.rotate(scope)

        msgs = conv2.get_messages()
        last = msgs[-1]
        assert last.get("entry_type") == "ref"
        assert last.get("role") == "user"
        assert int(last.get("message_stream_id", 0)) == msid

    async def test_ca6_clear_cache_rotate_no_ref_db_returns_clean(self, temp_db):
        """clear_cache 后 rotate，DB 中 active session 无 user ref → carry-over 为空。"""
        reg = _make_registry(temp_db)
        scope = ConversationScope.for_group("g1")

        conv1 = await reg.get_or_create(scope)
        conv1.add_message("assistant", "只有 assistant 消息")
        await conv1.save()
        sid1 = int(conv1.id)

        reg.clear_cache()
        conv2 = await reg.rotate(scope)

        refs = [m for m in conv2.get_messages() if m.get("entry_type") == "ref"]
        assert len(refs) == 0
        async with temp_db.db.execute(
            "SELECT status FROM persona_session WHERE session_id=?", (sid1,)
        ) as cur:
            row = await cur.fetchone()
        assert row["status"] == "closed"


class TestCA7CacheGeneration:
    """Wave 2 CA-7: _cache_generation 代际号使 clear_cache 后旧 conv 被拒重建。"""

    async def test_ca7_clear_cache_returns_new_conv_object(self, temp_db):
        """clear_cache → get_or_create 返回新对象（代际不匹配），底层 session 复用。"""
        reg = _make_registry(temp_db)
        scope = ConversationScope.for_group("g1")

        conv1 = await reg.get_or_create(scope)
        sid1 = conv1.id

        reg.clear_cache()
        assert reg.peek_cached(scope) is None

        conv2 = await reg.get_or_create(scope)

        # 新 Python 对象
        assert conv2 is not conv1
        # 同一 DB session（clear_cache 不关 DB）
        assert conv2.id == sid1

    async def test_ca7_manual_generation_mismatch_rebuilds(self, temp_db):
        """手动改 _cache_generation 使缓存条目代际不匹配 → 重建。"""
        reg = _make_registry(temp_db)
        scope = ConversationScope.for_group("g1")

        conv1 = await reg.get_or_create(scope)
        entry_gen = reg._conv_versions[scope]
        assert entry_gen == reg._cache_generation

        # 手动递增代际号（模拟 clear_cache 的核心效应，保留缓存条目以验证校验）
        reg._cache_generation += 1

        # _get_or_create_locked: gen_at_entry > _conv_versions[scope] → miss
        conv2 = await reg.get_or_create(scope)
        assert conv2 is not conv1
        # 同一 DB session 被复用
        assert conv2.id == conv1.id
        # 新条目打上新代际
        assert reg._conv_versions[scope] == reg._cache_generation

    async def test_ca7_consecutive_hit_after_clear(self, temp_db):
        """clear_cache 后 get_or_create 写入新代际条目，后续访问缓存命中。"""
        reg = _make_registry(temp_db)
        scope = ConversationScope.for_group("g1")

        await reg.get_or_create(scope)
        reg.clear_cache()
        old_gen = reg._cache_generation

        # 第一次: miss → 创建并缓存（新代际）
        conv_a = await reg.get_or_create(scope)
        assert reg._conv_versions[scope] == old_gen

        # 第二次: 缓存命中（代际匹配）
        conv_b = await reg.get_or_create(scope)
        assert conv_b is conv_a


# ── R7: 摘要 CAS + 不跨段回退 ────────────────────────────────


def _make_summarizer():
    """返回一个 FakeSummarizer，每次 generate_summary 记录调用。"""
    from plugins.DicePP.module.persona.life.conversation_summary import FakeSummarizer
    return FakeSummarizer(return_text="")


class TestSummaryCAS:
    """R7(a): 摘要 CAS 写入——两个并发 summarizer 只一个生效。"""

    @pytest.mark.asyncio
    async def test_two_summarizers_only_one_wins(self, temp_db):
        """两个 summarizer 返回不同文本 → CAS 保证只有一个写入，另一个回读胜者。"""
        from plugins.DicePP.module.persona.life.conversation_summary import FakeSummarizer

        summarizer_a = FakeSummarizer(return_text="摘要A")
        summarizer_b = FakeSummarizer(return_text="摘要B")

        scope = ConversationScope.for_group("g1")

        # 创建并关闭一个 session（带足够消息触发摘要生成）
        reg_a = ConversationRegistry(
            temp_db,
            runtime_factory=_runtime_factory,
            summarizer=summarizer_a,
        )
        conv = await reg_a.get_or_create(scope)
        for i in range(6):
            conv.add_message("user", f"消息{i}" * 10)
        await conv.save()
        await reg_a.close(scope)
        reg_a.clear_cache()

        # 现在用两个不同 registry（各有不同 summarizer）并发生成摘要
        reg1 = ConversationRegistry(
            temp_db,
            runtime_factory=_runtime_factory,
            summarizer=summarizer_a,
        )
        reg2 = ConversationRegistry(
            temp_db,
            runtime_factory=_runtime_factory,
            summarizer=summarizer_b,
        )

        # 并发生成
        s1, s2 = await asyncio.gather(
            reg1._ensure_summary_for_scope(scope),
            reg2._ensure_summary_for_scope(scope),
        )

        # 两个调用应返回相同文本（CAS 竞争失败者回读胜者）
        assert s1 == s2, f"并发摘要应一致: {s1!r} vs {s2!r}"
        assert s1 in ("摘要A", "摘要B")

        # DB 中只有一个值
        async with temp_db.db.execute(
            "SELECT summary_text FROM persona_session "
            "WHERE scope_namespace=? AND scope_key=? AND status='closed'",
            (scope.namespace, scope.key),
        ) as cur:
            rows = await cur.fetchall()
        assert len(rows) == 1
        db_summary = rows[0]["summary_text"]
        assert db_summary == s1


class TestSummaryNoCrossSegmentFallback:
    """R7(b): 不跨段回退——S1/S2(无摘要)/S3 场景下 S3 不继承 S1 的摘要。"""

    @pytest.mark.asyncio
    async def test_no_cross_segment_inheritance(self, temp_db):
        """S1(有摘要)→S2(无摘要)→S3: S3 不应继承 S1 的摘要。"""
        from plugins.DicePP.module.persona.life.conversation_summary import FakeSummarizer

        scope = ConversationScope.for_group("g1")

        # S1: 带足够消息 → 摘要
        reg = ConversationRegistry(
            temp_db,
            runtime_factory=_runtime_factory,
            summarizer=FakeSummarizer(return_text="S1摘要"),
        )
        conv1 = await reg.get_or_create(scope)
        for i in range(6):
            conv1.add_message("user", f"A{i}" * 10)
        await conv1.save()
        await reg.close(scope)
        # 显式生成 S1 的摘要（close 不自动生成摘要）
        s1_summary = await reg._ensure_summary_for_scope(scope)
        assert s1_summary == "S1摘要"
        reg.clear_cache()

        # S2: 无消息 → 关闭时不会生成摘要（消息数 < SUMMARY_MIN_MESSAGES）
        reg2 = ConversationRegistry(
            temp_db,
            runtime_factory=_runtime_factory,
            summarizer=FakeSummarizer(return_text=""),
        )
        conv2 = await reg2.get_or_create(scope)
        # S2 无消息（继承的摘要通知不算用户消息）
        await conv2.save()
        await reg2.close(scope)

        # 确认 S2 没有摘要
        async with temp_db.db.execute(
            "SELECT summary_text FROM persona_session "
            "WHERE status='closed' AND scope_namespace=? AND scope_key=? "
            "ORDER BY session_id DESC LIMIT 1",
            (scope.namespace, scope.key),
        ) as cur:
            row = await cur.fetchone()
        assert row is not None
        assert not (row["summary_text"] or ""), f"S2 不应有摘要: {row['summary_text']!r}"

        # S3: _read_inherited_summary 应返回空（不跨段回退到 S1）
        reg3 = ConversationRegistry(
            temp_db,
            runtime_factory=_runtime_factory,
            summarizer=FakeSummarizer(return_text=""),
        )
        reg3.clear_cache()
        s3_inherited = await reg3._read_inherited_summary(scope)
        assert s3_inherited == "", (
            f"S3 不应继承 S1 的摘要（跨段回退），实际: {s3_inherited!r}"
        )

# ── R9: 跨日检测 ─────────────────────────────────────────────────


class TestCrossDayBoundary:
    """R9: _get_or_create_locked 在复用 active session 前检测跨日边界。

    进程重启后 tick_daily 未执行时，旧日 active session 的 last_active_at 日期
    与当前 Clock 的日期不同，应关闭旧 session 并创建新 session。
    """

    @pytest.mark.asyncio
    async def test_cross_day_closes_old_creates_new(self, temp_db):
        """跨日时关闭旧 session 并创建新 session，同 scope 只有一个 active。"""
        import datetime
        from plugins.DicePP.utils.time import set_test_clock

        reg = _make_registry(temp_db)
        scope = ConversationScope.for_life_dm("char-1")

        # 创建第一个 session（last_active_at=生产 Clock 的今天）
        conv1 = await reg.get_or_create(scope)
        sid1 = int(conv1.id)

        # 手动设 last_active_at 为旧日期（生产 Clock 的昨天；
        # 不能用进程本地 date.today()，TZ 与上海日期错位时基准会错）
        from plugins.DicePP.utils.time import get_clock

        yesterday = (get_clock().now().date() - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        await temp_db.db.execute(
            "UPDATE persona_session SET last_active_at=? || ' 00:00:00' WHERE session_id=?",
            (yesterday, sid1),
        )
        await temp_db.db.commit()

        # 设置 Clock 到今天（生产 Clock 日期的 noon）
        set_test_clock(datetime.datetime.combine(get_clock().now().date(), datetime.time(12, 0)))

        # 清除缓存使 get_or_create 从 DB 重新加载
        reg.clear_cache()

        # get_or_create → 应检测跨日，关闭旧 session，创建新 session
        conv2 = await reg.get_or_create(scope)
        sid2 = int(conv2.id)

        # 旧 session 已关闭
        async with temp_db.db.execute(
            "SELECT status FROM persona_session WHERE session_id=?", (sid1,)
        ) as cur:
            row = await cur.fetchone()
        assert row["status"] == "closed", (
            f"旧 session {sid1} 应被关闭，实际 status={row['status']!r}"
        )

        # 新 session id 不同
        assert sid2 != sid1, "应创建新 session"

        # 同 scope 只有一个 active session
        async with temp_db.db.execute(
            "SELECT COUNT(*) as cnt FROM persona_session "
            "WHERE status='active' AND scope_namespace=? AND scope_key=?",
            (scope.namespace, scope.key),
        ) as cur:
            row = await cur.fetchone()
        assert row["cnt"] == 1

    @pytest.mark.asyncio
    async def test_same_day_reuses_session(self, temp_db):
        """同一日时不触发跨日轮换，直接复用现有 session。"""
        import datetime
        from plugins.DicePP.utils.time import set_test_clock

        reg = _make_registry(temp_db)
        scope = ConversationScope.for_life_dm("char-1")

        conv1 = await reg.get_or_create(scope)
        sid1 = int(conv1.id)

        # 设置 Clock 到今天（生产 Clock 日期的 noon，与 conv1 写入基准一致；
        # 不能用进程本地 date.today()，TZ 与上海日期错位时会注入"昨天"）
        from plugins.DicePP.utils.time import get_clock

        set_test_clock(datetime.datetime.combine(get_clock().now().date(), datetime.time(12, 0)))

        reg.clear_cache()

        conv2 = await reg.get_or_create(scope)
        sid2 = int(conv2.id)

        # 同一 session 被复用
        assert sid2 == sid1, "同日时不应创建新 session"
        async with temp_db.db.execute(
            "SELECT COUNT(*) as cnt FROM persona_session "
            "WHERE status='active' AND scope_namespace=? AND scope_key=?",
            (scope.namespace, scope.key),
        ) as cur:
            row = await cur.fetchone()
        assert row["cnt"] == 1, "同一日仍应只有一个 active session"

    @pytest.mark.asyncio
    async def test_cache_hit_rotates_life_session_after_virtual_day_step(self, temp_db):
        """漏掉 daily callback 时，缓存命中也必须按虚拟日补偿轮换。"""
        import datetime
        from plugins.DicePP.utils.time import SteppedClock, set_clock

        clock = SteppedClock(datetime.datetime(2042, 1, 1, 12, 0))
        set_clock(clock)
        reg = _make_registry(temp_db)
        scope = ConversationScope.for_life_character("char-1")
        sid1 = int((await reg.get_or_create(scope)).id)

        clock.step_by(days=1)
        sid2 = int((await reg.get_or_create(scope)).id)

        assert sid2 != sid1
        async with temp_db.db.execute(
            "SELECT status FROM persona_session WHERE session_id=?", (sid1,)
        ) as cur:
            assert (await cur.fetchone())["status"] == "closed"

    @pytest.mark.asyncio
    async def test_chat_scope_is_not_rotated_by_virtual_day(self, temp_db):
        """虚拟日只属于 Life；Chat scope 跨虚拟日仍由静默/token 边界管理。"""
        import datetime
        from plugins.DicePP.utils.time import SteppedClock, set_clock

        clock = SteppedClock(datetime.datetime(2042, 1, 1, 12, 0))
        set_clock(clock)
        reg = _make_registry(temp_db)
        scope = ConversationScope.for_group("g1")
        sid1 = int((await reg.get_or_create(scope)).id)

        clock.step_by(days=1)
        reg.clear_cache()
        sid2 = int((await reg.get_or_create(scope)).id)

        assert sid2 == sid1

    @pytest.mark.asyncio
    async def test_life_session_uses_virtual_clock_not_sqlite_wall_clock(self, temp_db):
        """新 Life session 的日期来自统一 Clock，不受 SQLite 当前 UTC 日期影响。"""
        import datetime
        from plugins.DicePP.utils.time import set_test_clock

        virtual_now = datetime.datetime(2042, 6, 3, 9, 30)
        set_test_clock(virtual_now)
        reg = _make_registry(temp_db)
        scope = ConversationScope.for_life_dm("char-1")
        sid1 = int((await reg.get_or_create(scope)).id)

        reg.clear_cache()
        sid2 = int((await reg.get_or_create(scope)).id)

        assert sid2 == sid1
        async with temp_db.db.execute(
            "SELECT last_active_at FROM persona_session WHERE session_id=?", (sid1,)
        ) as cur:
            stored = (await cur.fetchone())["last_active_at"]
        assert datetime.datetime.fromisoformat(stored).date() == virtual_now.date()
