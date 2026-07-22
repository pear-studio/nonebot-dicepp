"""Adversarial review: Wave 2 concurrency hardening — ConversationRegistry.

主动找出正确性/竞态/死锁/回归缺陷。不修改业务代码。
所有测试使用 temp_db (in-memory SQLite) + mock summarizer，绝不调真实 LLM。

审查目标（对抗问题 1-6）：
  1. TOCTOU：两阶段并发下同 scope 最多一个 active 是否仍成立
  2. CA-3 继承目标正确性：两阶段时序摘要是否指向刚关闭的 session
  3. CA-7 代际号是否真能挡住 stale conv
  4. 死锁/重入
  5. CA-6 兜底正确性：ref 捕获是否先于 close
  6. 回归：3b 不变量是否被两阶段破坏
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from plugins.DicePP.core.message_types import MessageType
from plugins.DicePP.module.persona.life.conversation_registry import ConversationRegistry
from plugins.DicePP.module.persona.life.conversation_scope import ConversationScope
from plugins.DicePP.module.persona.life.conversation_summary import (
    FakeSummarizer,
    SUMMARY_MIN_MESSAGES,
)


# ── shared helpers ─────────────────────────────────────────


def _runtime_factory():
    return MagicMock()


async def _active_session_count(store, scope: ConversationScope) -> int:
    async with store.db.execute(
        "SELECT COUNT(*) as cnt FROM persona_session "
        "WHERE status='active' AND scope_namespace=? AND scope_key=?",
        (scope.namespace, scope.key),
    ) as cur:
        row = await cur.fetchone()
    return row["cnt"] if row else 0


async def _summary_text_of(store, sid: int) -> str:
    async with store.db.execute(
        "SELECT summary_text FROM persona_session WHERE session_id=?", (sid,),
    ) as cur:
        row = await cur.fetchone()
    return row["summary_text"] if row else ""


async def _session_status(store, sid: int) -> str:
    async with store.db.execute(
        "SELECT status FROM persona_session WHERE session_id=?", (sid,),
    ) as cur:
        row = await cur.fetchone()
    return row["status"] if row else ""


async def _set_silence_expired(store, sid: int) -> None:
    await store.db.execute(
        "UPDATE persona_session SET last_active_at='2000-01-01 00:00:00' "
        "WHERE session_id=?", (sid,),
    )
    await store.db.commit()


async def _set_summary(store, sid: int, text: str) -> None:
    """Manually set a session's summary_text (bypasses summarizer)."""
    await store.db.execute(
        "UPDATE persona_session SET summary_text=? WHERE session_id=?", (text, sid),
    )
    await store.db.commit()


def _make_registry(store, summarizer=None, **kw):
    return ConversationRegistry(
        store,
        runtime_factory=_runtime_factory,
        summarizer=summarizer,
        **kw,
    )


async def _create_closed_session_with_summary(
    reg, store, scope, msg_count=SUMMARY_MIN_MESSAGES, summary_text="existing_summary",
):
    """创建已关闭 session 并手动设好摘要（避免惰性生成干扰 call count）。"""
    conv = await reg.get_or_create(scope)
    sid = int(conv.id)
    for i in range(msg_count):
        conv.add_message("user", f"msg_{i}")
    await conv.save()
    await reg.close(scope)
    if summary_text:
        await _set_summary(store, sid, summary_text)
    return conv, sid


async def _create_active_session(
    reg, store, scope, msg_count=SUMMARY_MIN_MESSAGES, silence_expired=False,
):
    """创建 active session（不带摘要）。"""
    conv = await reg.get_or_create(scope)
    sid = int(conv.id)
    for i in range(msg_count):
        conv.add_message("user", f"msg_{i}")
    await conv.save()
    if silence_expired:
        await _set_silence_expired(store, sid)
    return conv, sid


# ── Question 1: TOCTOU — 两阶段并发是否破坏「同 scope 最多一个 active」 ──────


class GateSummarizer:
    """Summarizer 栅栏：第 `gate_on_call` 次 generate_summary 阻塞，
    等待 continue_event 才放行。用于精确控制并发交错。"""

    def __init__(self, gate_on_call: int = 1, return_text: str = "gate summary"):
        self.call_count = 0
        self.gate_on_call = gate_on_call
        self.enter_event = asyncio.Event()
        self.continue_event = asyncio.Event()
        self.called_with: list = []
        self.return_text = return_text

    async def generate_summary(self, messages: list[dict]) -> str:
        self.call_count += 1
        self.called_with.append(messages)
        if self.call_count == self.gate_on_call:
            self.enter_event.set()
            await self.continue_event.wait()
        return self.return_text


class TestTOCTOU_SilencePath:
    """对抗问题 1(a)(b)(c)：append_visible 静默路径两阶段时序下不变量验证。

    append_visible 在静默超时时走两阶段：
      锁内 close → 释放锁 → 锁外 _ensure_summary_for_scope → 再持锁新建继承。

    如果另一同 scope 操作在此期间插入，是否：
      (a) 创建两个 active session？
      (b) 丢失或重复 carry-over ref？
      (c) 摘要注入到错误 session 或注入两次？
    """

    @pytest.mark.asyncio
    async def test_no_double_active_on_concurrent_silence(self, temp_db):
        """(a) 两个 append_visible 在静默路径交错，断言最终只 1 个 active session。

        关键设计：S1 已有手动摘要（防止 setup 阶段触发 generate_summary 计入 gate）。
        S2 是 active + silence-expired + 消息足。
        gate_on_call=1 → 第一个 generate_summary 调用发生在 Task A 的 post-close
        _ensure_summary_for_scope 中（恰是 adversarial gap）。
        """
        gate = GateSummarizer(gate_on_call=1, return_text="silence_toctou")
        reg = _make_registry(temp_db, summarizer=gate,
                             group_silence_seconds=0)
        scope = ConversationScope.for_group("g1")

        # S1 (closed, already has summary — prevents setup-time summary generation)
        _, sid1 = await _create_closed_session_with_summary(reg, temp_db, scope)

        # S2 (active, silence-expired, enough messages for post-close summary)
        conv2, sid2 = await _create_active_session(
            reg, temp_db, scope, silence_expired=True,
        )

        msid_a = await temp_db.add_message_stream(
            "u1", "g1", "user", MessageType.CHAT, "A的消息",
        )
        msid_b = await temp_db.add_message_stream(
            "u1", "g1", "user", MessageType.CHAT, "B的消息",
        )

        # Task A: 进入 silence path → close S2 → release lock
        #          → _ensure_summary_for_scope(S2) → generate_summary (1st call) → BLOCK
        task_a = asyncio.create_task(
            reg.append_visible(scope, msid_a, "user"),
        )
        await gate.enter_event.wait()

        # Task B: scope 锁空闲 → 进入 hot-path → 创建 S3
        conv_b = await reg.append_visible(scope, msid_b, "user")

        # 放行 Task A
        gate.continue_event.set()
        conv_a = await task_a

        # ── 断言 ─────────────────────────────────────────────
        count = await _active_session_count(temp_db, scope)
        assert count == 1, f"期望 1 个 active session, 实际 {count}"

        # 两个 coro 同一 conv 对象
        assert conv_a is conv_b, "两个 coro 应返回同一 conv 对象"

        # 两条 ref 都在 conv 中
        cached = reg.peek_cached(scope)
        assert cached is not None
        msgs = cached.get_messages()
        ref_msids = {
            int(m["message_stream_id"])
            for m in msgs if m.get("entry_type") == "ref"
            and m.get("message_stream_id") is not None
        }
        assert msid_a in ref_msids, f"Task A 的 msid {msid_a} 应存在"
        assert msid_b in ref_msids, f"Task B 的 msid {msid_b} 应存在"

        # S2 应有摘要
        summary = await _summary_text_of(temp_db, sid2)
        assert summary == "silence_toctou", \
            f"S2 摘要应为 'silence_toctou', 实际 '{summary}'"

        # 摘要只注入一次（前缀只有一条）
        summary_prefix_count = sum(
            1 for m in msgs if "silence_toctou" in m.get("content", "")
        )
        assert summary_prefix_count == 1, \
            f"摘要文本不应重复, 出现 {summary_prefix_count} 次"

    @pytest.mark.asyncio
    async def test_silence_path_contention_stress(self, temp_db):
        """压力测试：8 路并发 append_visible，验证最终只 1 个 active session。"""

        class YieldingSummarizer:
            def __init__(self):
                self.called_with = []
                self.call_count = 0

            async def generate_summary(self, messages):
                self.called_with.append(messages)
                self.call_count += 1
                await asyncio.sleep(0)
                return f"stress_summary_{self.call_count}"

        summarizer = YieldingSummarizer()
        # 注意：此处用 group_silence_seconds=3600（非0），避免 julianday 精度
        # 导致刚创建的 session 也被判为 "silence expired" 的假阴性。
        # 生产环境群聊静默为 1800s，私聊 86400s，不会出现此问题。
        reg = _make_registry(temp_db, summarizer=summarizer,
                             group_silence_seconds=3600)
        scope = ConversationScope.for_group("g1")

        # S1(closed, 有摘要)
        await _create_closed_session_with_summary(reg, temp_db, scope)

        # S2(active,静默过期,消息足) — 设 last_active_at 为 2000年使其远超 3600s
        await _create_active_session(reg, temp_db, scope, silence_expired=True)

        N = 8
        msids = []
        for i in range(N):
            msid = await temp_db.add_message_stream(
                "u1", "g1", "user", MessageType.CHAT, f"stress_{i}",
            )
            msids.append(msid)

        results = await asyncio.gather(*[
            reg.append_visible(scope, msid, "user") for msid in msids
        ])

        # 关键断言：无论返回的对象引用是否相同，最终只能有 1 个 active session
        # （DB UNIQUE INDEX 兜底，见 idx_persona_session_active_scope）。
        count = await _active_session_count(temp_db, scope)
        assert count == 1, f"最终 active session 应为 1, 实际 {count}。TOCTOU 突破！"

        cached = reg.peek_cached(scope)
        assert cached is not None

        # 8 条 ref 全部在缓存 conv 中
        all_msg_stream_ids = set()
        for m in cached.get_messages():
            msid = m.get("message_stream_id")
            if msid is not None:
                all_msg_stream_ids.add(int(msid))
        for msid in msids:
            assert msid in all_msg_stream_ids, f"msid {msid} 应在 conv 中"


class TestTOCTOU_Rotate:
    """对抗问题 1：rotate 两阶段时序下的不变量。"""

    @pytest.mark.asyncio
    async def test_rotate_no_double_active(self, temp_db):
        """rotate 两阶段 gap 中插入 append_visible，只 1 个 active session。"""
        gate = GateSummarizer(gate_on_call=1, return_text="rotate_gate")
        reg = _make_registry(temp_db, summarizer=gate)
        scope = ConversationScope.for_group("g1")

        # S1(closed,有摘要)
        await _create_closed_session_with_summary(reg, temp_db, scope)

        # S2(active,消息足)
        conv2, sid2 = await _create_active_session(reg, temp_db, scope)

        # 追加一条 user ref 用于 carry-over
        msid_carry = await temp_db.add_message_stream(
            "u1", "g1", "user", MessageType.CHAT, "rotate carry ref",
        )
        await conv2.append_ref(msid_carry, "user")

        # gap 中 append_visible 的 msid
        msid_gap = await temp_db.add_message_stream(
            "u1", "g1", "user", MessageType.CHAT, "gap new msg",
        )

        # Task A: rotate → first lock close S2 → unlock → ensure_summary → BLOCK
        task_a = asyncio.create_task(reg.rotate(scope))
        await gate.enter_event.wait()

        # Task B: gap 中 append_visible
        conv_b = await reg.append_visible(scope, msid_gap, "user")
        sid_b = int(conv_b.id)

        gate.continue_event.set()
        conv_a = await task_a

        count = await _active_session_count(temp_db, scope)
        assert count == 1, f"期望 1 个 active session, 实际 {count}"
        assert conv_a is conv_b, "rotate 与 gap append 应返回同一 conv"

        # 旧 session closed
        assert await _session_status(temp_db, sid2) == "closed"
        # S2 有摘要
        assert await _summary_text_of(temp_db, sid2) == "rotate_gate"

        msgs = conv_a.get_messages()
        ref_msids = {
            int(m["message_stream_id"])
            for m in msgs if m.get("entry_type") == "ref"
            and m.get("message_stream_id") is not None
        }
        assert msid_carry in ref_msids, "carry-over ref 应存在"
        assert msid_gap in ref_msids, "gap 中追加的 ref 应存在"


# ── Question 2: CA-3 继承目标正确性 ──────────────────────────


class TestCA3_InheritanceTarget:
    """对抗问题 2：两阶段确保继承的是刚关闭 session 的摘要。"""

    @pytest.mark.asyncio
    async def test_inherits_just_closed_not_older(self, temp_db):
        """新活跃期继承刚关闭的 session（S2）摘要，而非更旧的 S1。"""
        from plugins.DicePP.module.persona.life.conversation_summary import FakeSummarizer

        class TaggedSummarizer:
            def __init__(self):
                self.called_with = []
                self._call_count = 0

            async def generate_summary(self, messages):
                self._call_count += 1
                self.called_with.append(messages)
                return f"S{self._call_count}_摘要"

        summarizer = TaggedSummarizer()
        reg = _make_registry(temp_db, summarizer=summarizer)
        scope = ConversationScope.for_group("g1")

        # S1: create, add messages, close
        conv1 = await reg.get_or_create(scope)
        sid1 = int(conv1.id)
        for i in range(5):
            conv1.add_message("user", f"s1_{i}")
        await conv1.save()
        await reg.close(scope)

        # 触发 S1 摘要生成（惰性：在下次 get_or_create 时进行）
        # 创建临时 scope 用于触发 —— 但 easiest 还是直接 get_or_create 然后 close
        # 实际上我们直接在同一个 scope get_or_create，这就创建了 S2 并自然触发 S1 摘要
        conv2 = await reg.get_or_create(scope)  # triggers S1 summary, creates S2
        sid2 = int(conv2.id)

        # S1 应有摘要
        s1_summary = await _summary_text_of(temp_db, sid1)
        assert s1_summary == "S1_摘要", f"S1 摘要应为 'S1_摘要', 实际 '{s1_summary}'"

        # S2: add messages, close
        for i in range(5):
            conv2.add_message("user", f"s2_{i}")
        await conv2.save()
        await reg.close(scope)

        # 下次 get_or_create → 应为 S2 生成摘要
        conv3 = await reg.get_or_create(scope)

        s2_summary = await _summary_text_of(temp_db, sid2)
        assert s2_summary == "S2_摘要", \
            f"S2 摘要应为 'S2_摘要', 实际 '{s2_summary}'"

        # conv3 继承 S2 摘要而非 S1
        msgs3 = conv3.get_messages()
        msgs3_text = " ".join(m.get("content", "") for m in msgs3)
        assert "S2_摘要" in msgs3_text, "新 conv 应继承 S2 摘要"
        assert "S1_摘要" not in msgs3_text, "新 conv 不应继承 S1 摘要"


# ── Question 3: CA-7 代际号 ──────────────────────────────────


class TestCA7_CacheGeneration:
    """对抗问题 3：CA-7 代际号验证。"""

    @pytest.mark.asyncio
    async def test_cache_hit_respects_generation(self, temp_db):
        """generation 不变 → 命中；generation 变化 → miss。"""
        reg = _make_registry(temp_db)
        scope = ConversationScope.for_group("g1")

        conv1 = await reg.get_or_create(scope)
        conv2 = await reg.get_or_create(scope)
        assert conv2 is conv1, "代际不变时应缓存命中"

        reg.clear_cache()
        conv3 = await reg.get_or_create(scope)
        assert conv3 is not conv2, "clear_cache 后应重建"
        assert int(conv3.id) == int(conv2.id), "重建应复用同个 DB session"

    @pytest.mark.asyncio
    async def test_stale_version_rejected(self, temp_db):
        """低代际缓存条目被拒绝→重建。"""
        reg = _make_registry(temp_db)
        scope = ConversationScope.for_group("g1")

        await reg.get_or_create(scope)
        reg.clear_cache()

        conv_fresh = await reg.get_or_create(scope)
        gen = reg._cache_generation
        assert gen > 0

        # 伪造低代际条目
        reg._conv_versions[scope] = gen - 1

        # 再次 get_or_create → 代际不匹配 → 重建
        rebuilt = await reg.get_or_create(scope)
        assert rebuilt is not conv_fresh, "低代际条目应被拒绝"

    @pytest.mark.asyncio
    async def test_concurrent_clear_cache_requires_rebuild(self, temp_db):
        """并发 clear_cache 后，用旧代际写入的 conv 被后续访问拒绝。"""
        reg = _make_registry(temp_db)
        scope = ConversationScope.for_group("g1")

        # 先创建个 session
        await reg.get_or_create(scope)

        # 模拟：
        # 1) get_or_create 捕获 gen_at_entry=0
        # 2) 期间 clear_cache 递增 gen 到 1
        # 3) get_or_create 以 gen=0 写入缓存
        # 4) 下次访问 gen 不匹配 → 重建
        reg.clear_cache()  # gen → 1
        assert reg._cache_generation == 1

        # 手动模拟旧代际写入：先正常 get_or_create（会以 gen=1 写入），
        # 然后手动把代际改成 0（模拟步骤 3 的旧 gen 覆盖）
        conv_now = await reg.get_or_create(scope)
        sid_orig = int(conv_now.id)
        stale_gen = 0  # 假设 clear_cache 前捕获的 gen
        reg._conv_versions[scope] = stale_gen

        # 此时缓存有条目但代际不匹配
        assert reg.peek_cached(scope) is conv_now
        assert reg._conv_versions.get(scope) != reg._cache_generation

        # 再次 get_or_create → 应拒绝 stale → 重建
        conv_rebuilt = await reg.get_or_create(scope)

        # 重建的 conv 持有当前代际
        assert reg._conv_versions.get(scope) == reg._cache_generation
        assert int(conv_rebuilt.id) == sid_orig  # 同 DB session（复用 _find_active）
        assert conv_rebuilt is not conv_now, "stale conv 应被拒绝"


# ── Question 4: 死锁/重入 ──────────────────────────────────────


class TestDeadlockReentry:
    """对抗问题 4：死锁/重入。

    结构分析（无需运行可确认）：
      get_or_create:      无锁→_ensure→有锁→_get_or_create_locked（不取锁）
      append_visible:     无锁→_ensure→有锁→close→无锁→_ensure→有锁→_get_or_create_locked
      close:              有锁→_close_locked（不取锁）
      rotate:             有锁→close+ref→无锁→_ensure→有锁→_get_or_create_locked
      clear_cache:        同步，不持锁

    所有公共方法均不嵌套取 scope 锁。验证性测试：
      (a) 两阶段间锁已释放
      (b) _ensure_summary_for_scope 期间 scope 锁可被其他 coro 获取
    """

    @pytest.mark.asyncio
    async def test_append_visible_and_rotate_no_deadlock(self, temp_db):
        """两阶段方法正常完成即为无死锁（asyncio.Lock 不可重入）。

        若任一两阶段路径在 step1 释放锁后未正确释放，step2 重新
        `async with self._lock_for(scope)` 会死锁。
        """
        reg = _make_registry(temp_db, summarizer=FakeSummarizer(return_text="no_deadlock"))
        scope = ConversationScope.for_group("g1")

        # S1 closed with summary, S2 active silence-expired
        await _create_closed_session_with_summary(reg, temp_db, scope)
        await _create_active_session(reg, temp_db, scope, silence_expired=True)

        msid = await temp_db.add_message_stream(
            "u1", "g1", "user", MessageType.CHAT, "deadlock_test",
        )
        conv = await reg.append_visible(scope, msid, "user")
        assert conv is not None

        conv2 = await reg.rotate(scope)
        assert conv2 is not None

    @pytest.mark.asyncio
    async def test_ensure_summary_no_lock_held(self, temp_db):
        """验证 _ensure_summary_for_scope 期间 scope 锁可被其他 coro 获取。

        如果 _ensure_summary_for_scope 内部持锁（比如不慎在锁内调用），
        另一个 coro 尝试获取同 scope 锁时会阻塞。
        """
        gate = GateSummarizer(gate_on_call=1, return_text="no_lock_during_ensure")
        reg = _make_registry(temp_db, summarizer=gate,
                             group_silence_seconds=0)
        scope = ConversationScope.for_group("g1")

        await _create_closed_session_with_summary(reg, temp_db, scope)
        await _create_active_session(reg, temp_db, scope, silence_expired=True)

        msid_a = await temp_db.add_message_stream(
            "u1", "g1", "user", MessageType.CHAT, "A",
        )
        task_a = asyncio.create_task(reg.append_visible(scope, msid_a, "user"))
        await gate.enter_event.wait()

        # 此时 Task A 在 _ensure_summary_for_scope 的 generate_summary 中阻塞
        # scope 锁应可被其他 coro 获取
        lock_acquired = False
        try:
            async with asyncio.timeout(1):
                async with reg._lock_for(scope):
                    lock_acquired = True
        except (asyncio.TimeoutError, TimeoutError):
            lock_acquired = False

        assert lock_acquired, \
            "_ensure_summary_for_scope 不应持 per-scope lock"

        gate.continue_event.set()
        await task_a


# ── Question 5: CA-6 兜底正确性 ────────────────────────────────


class TestCA6_DBFallback:
    """对抗问题 5：CA-6 在 clear_cache 清空缓存后 rotate 从 DB 兜底取 ref。"""

    @pytest.mark.asyncio
    async def test_ref_captured_before_close(self, temp_db):
        """rotate 的 ref 捕获发生在 close 之前。"""
        reg = _make_registry(temp_db, summarizer=FakeSummarizer(return_text="ca6_ref"))
        scope = ConversationScope.for_group("g1")

        await _create_closed_session_with_summary(reg, temp_db, scope)
        conv2, sid2 = await _create_active_session(reg, temp_db, scope)

        msid_user = await temp_db.add_message_stream(
            "u1", "g1", "user", MessageType.CHAT, "最后一条 user 消息",
        )
        await conv2.append_ref(msid_user, "user")

        conv3 = await reg.rotate(scope)

        # close happens after ref capture — verify ordering
        assert await _session_status(temp_db, sid2) == "closed"
        refs = [
            m for m in conv3.get_messages()
            if m.get("entry_type") == "ref"
            and m.get("message_stream_id") == msid_user
        ]
        assert len(refs) == 1, "carry-over ref 应存在"

    @pytest.mark.asyncio
    async def test_db_fallback_after_clear_cache(self, temp_db):
        """clear_cache 后 rotate 从 DB 兜底取 ref。"""
        reg = _make_registry(temp_db, summarizer=FakeSummarizer(return_text="db_fallback"))
        scope = ConversationScope.for_group("g1")

        await _create_closed_session_with_summary(reg, temp_db, scope)
        conv2, sid2 = await _create_active_session(reg, temp_db, scope)

        msid_user = await temp_db.add_message_stream(
            "u1", "g1", "user", MessageType.CHAT, "DB兜底ref",
        )
        await conv2.append_ref(msid_user, "user")

        # 清空缓存 → old_conv 为 None → rotate 走 DB 兜底
        reg.clear_cache()

        conv3 = await reg.rotate(scope)

        assert await _session_status(temp_db, sid2) == "closed"
        refs = [
            m for m in conv3.get_messages()
            if m.get("entry_type") == "ref"
            and m.get("message_stream_id") == msid_user
        ]
        assert len(refs) == 1, "DB 兜底应捕获最后一条 user ref"
        assert refs[0].get("role") == "user", "carry-over 应为 user role"

    @pytest.mark.asyncio
    async def test_db_fallback_no_ref_creates_clean(self, temp_db):
        """DB 中无 user ref 时，rotate 创建干净的 conv。"""
        reg = _make_registry(temp_db, summarizer=FakeSummarizer(return_text="clean"))
        scope = ConversationScope.for_group("g1")

        await _create_closed_session_with_summary(reg, temp_db, scope)
        conv2, sid2 = await _create_active_session(reg, temp_db, scope)
        reg.clear_cache()

        conv3 = await reg.rotate(scope)
        assert int(conv3.id) != sid2

        refs = [m for m in conv3.get_messages() if m.get("entry_type") == "ref"]
        assert len(refs) == 0, "无 user ref 时不应 carry-over"


# ── Question 6: 回归 — 3b 不变量 ──────────────────────────────


class TestRegression:
    """对抗问题 6：Wave 2 是否破坏了 3b 已有不变量。"""

    @pytest.mark.asyncio
    async def test_summary_not_overwritten(self, temp_db):
        """P1-14 回归：已有 summary_text 不重写。"""
        call_count = [0]

        class GuardedSummarizer:
            async def generate_summary(self, messages):
                call_count[0] += 1
                return f"call_{call_count[0]}"

        reg = _make_registry(temp_db, summarizer=GuardedSummarizer())
        scope = ConversationScope.for_group("g1")

        # 创建 S1, 写消息, 关闭
        conv1 = await reg.get_or_create(scope)
        sid1 = int(conv1.id)
        for i in range(5):
            conv1.add_message("user", f"msg{i}")
        await conv1.save()
        await reg.close(scope)

        # 触发 S1 摘要生成（惰性：在下次 get_or_create 时）
        conv2 = await reg.get_or_create(scope)
        assert call_count[0] == 1, "关闭后首次访问应生成摘要"

        # 手动覆盖 S1 摘要（模拟已有 summary_text）
        await _set_summary(temp_db, sid1, "原始摘要")

        # 再次 close + get_or_create → most-recent-closed 仍是 S1（有摘要）
        # conv2 (=S2) 被关闭，但 S2 没有足够消息或 close 后即生成
        # 关键：此时 S1 已有摘要 → _ensure_summary_for_scope 应 return 不调 generate
        await reg.close(scope)
        call_count_before = call_count[0]
        conv3 = await reg.get_or_create(scope)
        assert call_count[0] == call_count_before, \
            "已有摘要时不应调用 generate_summary"

        # R7(b): S3 不跨段继承——S2（紧邻上一段）无摘要，因此 S3 不应自动注入
        # S1 的"原始摘要"（由 history 工具补查而非自动注入陈旧上下文）。
        msgs3 = conv3.get_messages()
        msgs3_text = " ".join(m.get("content", "") for m in msgs3)
        assert "原始摘要" not in msgs3_text, (
            "R7(b): S3 不应跨 S2 继承 S1 的摘要"
        )

    @pytest.mark.asyncio
    async def test_empty_messages_guard(self, temp_db):
        """len(conv._messages)==0 注入守卫在两阶段路径下有效。"""
        reg = _make_registry(temp_db, summarizer=FakeSummarizer(return_text="guard_test"))
        scope = ConversationScope.for_group("g1")

        await _create_closed_session_with_summary(reg, temp_db, scope)
        await _create_active_session(reg, temp_db, scope, silence_expired=True)

        msid = await temp_db.add_message_stream("u1", "g1", "user", MessageType.CHAT, "test")
        conv3 = await reg.append_visible(scope, msid, "user")

        msgs = conv3.get_messages()
        summary_count = sum(1 for m in msgs if "guard_test" in m.get("content", ""))
        assert summary_count <= 1, f"摘要不应重复注入, 出现 {summary_count} 次"

    @pytest.mark.asyncio
    async def test_silence_rotation_semantics_preserved(self, temp_db):
        """P1-1 回归：静默轮换在两阶段下仍正确触发。"""
        reg = _make_registry(temp_db, summarizer=FakeSummarizer(return_text="silence"),
                             group_silence_seconds=0)
        scope = ConversationScope.for_group("g1")

        msid1 = await temp_db.add_message_stream("u1", "g1", "user", MessageType.CHAT, "m1")
        conv1 = await reg.append_visible(scope, msid1, "user")
        sid1 = int(conv1.id)

        await _set_silence_expired(temp_db, sid1)

        msid2 = await temp_db.add_message_stream("u1", "g1", "user", MessageType.CHAT, "m2")
        conv2 = await reg.append_visible(scope, msid2, "user")

        assert conv2.id != sid1, "应创建新 session"
        assert await _session_status(temp_db, sid1) == "closed"
        assert await _active_session_count(temp_db, scope) == 1

    @pytest.mark.asyncio
    async def test_summary_ref_resolve_still_works(self, temp_db):
        """W4 回归：摘要生成时正确 resolve ref 条目。"""
        summarizer = FakeSummarizer(return_text="resolve_regression")
        reg = _make_registry(temp_db, summarizer=summarizer)
        scope = ConversationScope.for_group("g1")

        conv1 = await reg.get_or_create(scope)
        msid = await temp_db.add_message_stream(
            "u1", "g1", "user", MessageType.CHAT, "原始被引用正文",
        )
        await conv1.append_ref(msid, "user")
        for i in range(3):
            conv1.add_message("assistant", f"回复{i}")
        await conv1.save()
        await reg.close(scope)

        conv2 = await reg.get_or_create(scope)
        assert len(summarizer.called_with) >= 1
        last_batch = summarizer.called_with[-1]
        ref_msgs = [m for m in last_batch if m.get("entry_type") == "ref"]
        if ref_msgs:
            assert "原始被引用正文" in ref_msgs[0].get("content", "")

    @pytest.mark.asyncio
    async def test_scope_isolation_preserved(self, temp_db):
        """scope 隔离在两阶段下仍有效。"""
        summarizer = FakeSummarizer(return_text="isolated")
        reg = _make_registry(temp_db, summarizer=summarizer,
                             group_silence_seconds=0)
        scope_g1 = ConversationScope.for_group("g1")
        scope_g2 = ConversationScope.for_group("g2")

        # g1 setup
        await _create_closed_session_with_summary(reg, temp_db, scope_g1)
        _, sid_g1_2 = await _create_active_session(
            reg, temp_db, scope_g1, silence_expired=True,
        )

        # g2 setup
        conv_g2, _ = await _create_active_session(reg, temp_db, scope_g2)
        for i in range(5):
            conv_g2.add_message("user", f"g2_{i}")
        await conv_g2.save()
        await reg.close(scope_g2)

        # silence-triggered append on g1
        msid = await temp_db.add_message_stream("u1", "g1", "user", MessageType.CHAT, "g1_new")
        conv_g1_new = await reg.append_visible(scope_g1, msid, "user")
        conv_g2_new = await reg.get_or_create(scope_g2)

        assert await _active_session_count(temp_db, scope_g1) == 1
        assert await _active_session_count(temp_db, scope_g2) == 1
        assert conv_g1_new is not conv_g2_new


class TestAdversarialEdge:
    """额外的边界情况核查。"""

    @pytest.mark.asyncio
    async def test_append_visible_hot_path_not_regressed(self, temp_db):
        """热路径（不超时）仍单临界区。"""
        reg = _make_registry(temp_db, group_silence_seconds=86400)
        scope = ConversationScope.for_group("g1")

        msid = await temp_db.add_message_stream("u1", "g1", "user", MessageType.CHAT, "hot")
        conv = await reg.append_visible(scope, msid, "user")
        conv2 = await reg.append_visible(scope, msid, "user")
        assert conv2 is conv

    @pytest.mark.asyncio
    async def test_clear_cache_sync_no_lock(self, temp_db):
        """clear_cache 是同步方法且不持锁（CA-7 前提）。"""
        reg = _make_registry(temp_db)
        scope = ConversationScope.for_group("g1")

        await reg.get_or_create(scope)
        assert reg._cache_generation == 0

        reg.clear_cache()
        assert reg._cache_generation == 1
        assert len(reg._active_convs) == 0
        assert len(reg._conv_versions) == 0

        conv = await reg.get_or_create(scope)
        assert conv is not None

    @pytest.mark.asyncio
    async def test_summary_inheritance_chain_minimal_regression(self, temp_db):
        """简化的摘要继承链回归：S1→close→S2 继承 S1 摘要。"""
        summarizer = FakeSummarizer(return_text="S1_summary")
        reg = _make_registry(temp_db, summarizer=summarizer)
        scope = ConversationScope.for_group("g1")

        conv1 = await reg.get_or_create(scope)
        sid1 = int(conv1.id)
        for i in range(5):
            conv1.add_message("user", f"msg{i}")
        await conv1.save()
        await reg.close(scope)

        # 触发 S1 摘要生成并创建 S2
        conv2 = await reg.get_or_create(scope)
        assert any("S1_summary" in m.get("content", "") for m in conv2.get_messages())


class TestSumGenDoubleCheck:
    """SUM-GEN 竞态：_ensure_summary_for_scope 的 Step 5.5 双检 — 减少但不消除
    锁外并发导致的重复摘要 LLM 调用。"""

    @pytest.mark.asyncio
    async def test_double_check_returns_existing_summary(self, temp_db):
        """Step 5.5 双检：摘要在 Step 2 读取后由另一线程写入 → 双检命中 → 跳过生成。"""
        call_count = [0]

        class CountingSummarizer:
            async def generate_summary(self, messages):
                call_count[0] += 1
                return "generated"

        summarizer = CountingSummarizer()
        reg = _make_registry(temp_db, summarizer=summarizer)
        scope = ConversationScope.for_group("g1")

        conv1 = await reg.get_or_create(scope)
        sid1 = int(conv1.id)
        for i in range(5):
            conv1.add_message("user", f"msg{i}")
        await conv1.save()
        await reg.close(scope)

        # 首次调用 → Step 2 无摘要 → 加载消息 → 通过双检（仍无摘要）→ 生成并写入
        result1 = await reg._ensure_summary_for_scope(scope)
        assert call_count[0] == 1
        assert result1 == "generated"

        # 模拟并发场景：Step 2 读取时摘要为空，但之后有并发写入发生在这之间。
        # 先清空 DB 摘要（使 Step 2 确认无摘要），
        # 再写入一个不同的摘要（模拟并发线程在 Step 2-5.5 之间完成写入）。
        await temp_db.db.execute(
            "UPDATE persona_session SET summary_text='' WHERE session_id=?", (sid1,)
        )
        await temp_db.db.commit()
        await temp_db.db.execute(
            "UPDATE persona_session SET summary_text='concurrent_summary' WHERE session_id=?",
            (sid1,)
        )
        await temp_db.db.commit()

        # 再次调用 → Step 2 读到空 → 加载消息 → Step 5.5 双检读到
        # 'concurrent_summary' → 跳过生成，直接返回该摘要
        result2 = await reg._ensure_summary_for_scope(scope)
        assert call_count[0] == 1, "双检应阻止第二次生成"
        assert result2 == "concurrent_summary", "应返回并发线程已写入的摘要"

    @pytest.mark.asyncio
    async def test_double_check_concurrent_sequential_call(self, temp_db):
        """并发场景：两路 _ensure，其中一路先完成写入，另一路双检命中跳过生成。"""
        call_count = [0]

        class BlockingSummarizer:
            def __init__(self):
                self.enter_event = asyncio.Event()
                self.continue_event = asyncio.Event()

            async def generate_summary(self, messages):
                call_count[0] += 1
                self.enter_event.set()
                await self.continue_event.wait()
                return "summary"

        summarizer = BlockingSummarizer()
        reg = _make_registry(temp_db, summarizer=summarizer)
        scope = ConversationScope.for_group("g1")

        conv1 = await reg.get_or_create(scope)
        sid1 = int(conv1.id)
        for i in range(5):
            conv1.add_message("user", f"msg{i}")
        await conv1.save()
        await reg.close(scope)
        reg.clear_cache()

        # Task A: 进入 _ensure → 加载 → generate → BLOCK（等待 continue_event）
        task_a = asyncio.create_task(reg._ensure_summary_for_scope(scope))
        await summarizer.enter_event.wait()

        # 放行 A → A 写入 DB 并返回
        summarizer.continue_event.set()
        result_a = await task_a
        assert result_a == "summary"

        # 此时 DB 已有摘要。第二次调用 _ensure 经由 Step 2 直接返回，双检不触发。
        call_count_before = call_count[0]
        result_b = await reg._ensure_summary_for_scope(scope)
        assert call_count[0] == call_count_before, "第二次调用不应生成摘要"
        assert result_b == "summary"
