"""ConversationRegistry — 按 scope 定位/创建/追加 Conversation 的深接口。

把"按 scope 定位、创建、追加、并发保护"藏在一个较深的接口后，消息接入方
（orchestrator / hook）不再各自组合 user_id/group_id 重复实现定位规则。

不变量：
- 同一 scope 同时最多一个 active Conversation（内存缓存 + DB partial unique index）。
- conversation_id 表示一个具体活跃期；同 scope 可顺序拥有多个 Conversation。
- 不同 scope 严格隔离（群/私聊/不同用户各自独立）。

并发：每个 scope 一把 asyncio.Lock；get_or_create / append_visible / close 在锁内
执行（ambient 路径不走 LLMCallCoordinator，必须自带锁）。

本模块不拥有 Agent 执行权：Conversation 仍由调用方持有并调用 run()。
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Callable, Optional

from plugins.DicePP.utils.logger import logger
from plugins.DicePP.utils.time import get_clock

from ..data.store import PersonaDataStore
from .conversation import DANGLING_REF_FALLBACK, NOTIFICATION_PREFIX, ChangeSource, Conversation
from .conversation_scope import ConversationScope
from .conversation_store import ConversationStore, _cell, _recompose_message
from .conversation_summary import SUMMARY_MIN_MESSAGES, Summarizer

# 依赖注入的工厂类型
RuntimeFactory = Callable[[], Any]
ChangeSourceFactory = Callable[[ConversationScope], "list[ChangeSource]"]
CharacterIdProvider = Callable[[], str]


class ConversationRegistry:
    """按 scope 管理 Conversation 活跃期的注册表。

    所有权契约（persona_session 生命周期）：
    - Registry 独占 chat 路径的 session 创建（`_create_session`）与关闭
      （`_close_locked`）。
    - ConversationStore.put/append 仅负责消息读写；其 put 的 create-session 分支
      只服务 Life 无-registry 路径（Conversation 直接持 store、`_id` 从 None 起，
      首次 `save()→put` 创建），在 chat 接线下不参与——registry 路径下
      `_get_or_create_locked` 总会先置 `conv._id`，`_persist_new` 恒走 append。
    维护者修 session 创建/关闭应改 Registry，修消息落盘应改 ConversationStore。
    """

    def __init__(
        self,
        store: PersonaDataStore,
        *,
        runtime_factory: RuntimeFactory,
        change_source_factory: Optional[ChangeSourceFactory] = None,
        character_id_provider: Optional[CharacterIdProvider] = None,
        summarizer: Optional[Summarizer] = None,
        private_silence_seconds: int = 86400,
        group_silence_seconds: int = 1800,
        on_scope_closed: Optional[Callable[[ConversationScope], Any]] = None,
    ) -> None:
        self._store = store
        self._runtime_factory = runtime_factory
        self._change_source_factory = change_source_factory or (lambda scope: [])
        self._character_id_provider = character_id_provider or (lambda: "")
        self._summarizer = summarizer
        self._private_silence_seconds = private_silence_seconds
        self._group_silence_seconds = group_silence_seconds
        # R12: scope 关闭回调（供 Orchestrator 清理 _agents 缓存）
        self._on_scope_closed = on_scope_closed
        self._active_convs: dict[ConversationScope, Conversation] = {}
        self._locks: dict[ConversationScope, asyncio.Lock] = {}
        # Conversation.run() 不持有 _locks：运行中 send_reply 的消息回流仍需进入
        # append_visible，若长期占用同一把锁会自锁。这里用独立的读写式生命周期门：
        # run lease 之间按 scope 串行；close/rotate 等 transition 阻止新 lease，并等待
        # 已有 lease 在 finally 中归还后再改变 session 生命周期。
        self._lifecycle_conditions: dict[ConversationScope, asyncio.Condition] = {}
        self._active_run_leases: set[ConversationScope] = set()
        self._lifecycle_transitions: set[ConversationScope] = set()
        # CA-7: 缓存代际号。clear_cache（角色切换）递增它，_get_or_create_locked 校验
        # 缓存条目的代际；跨代际的旧 conv 视为 miss 重建，防止用旧角色 runtime/change
        # source 构建的 Conversation 存活到切换之后。
        self._conv_versions: dict[ConversationScope, int] = {}
        self._cache_generation: int = 0

    # ── 公开接口 ─────────────────────────────────────────

    async def get_or_create(self, scope: ConversationScope) -> Conversation:
        """按 scope 定位/创建 active Conversation（缓存命中即返回）。"""
        # CA-3: 摘要 LLM 调用移出 per-scope 锁——仅在可能创建新活跃期时（无缓存）于锁外
        # 预生成同 scope 最近 closed session 的摘要并落库，锁内 _get_or_create_locked 只
        # 做廉价读取注入。避免锁内最长 30s 的 LLM 调用阻塞同 scope 所有操作。
        if self.peek_cached(scope) is None:
            await self._ensure_summary_for_scope(scope)
        # Life 的 get_or_create 可能因跨虚拟日关闭旧 session；非 run 调用也必须
        # 尊重正在执行的 lease。Chat scope 的纯定位不改变生命周期，保留热路径。
        if scope.is_life:
            async with self._lifecycle_transition(scope):
                async with self._lock_for(scope):
                    return await self._get_or_create_locked(scope)
        async with self._lock_for(scope):
            return await self._get_or_create_locked(scope)

    @asynccontextmanager
    async def run_guard(
        self, scope: ConversationScope,
    ) -> AsyncIterator[None]:
        """取得 scope 的 run 生命周期权；不负责定位 Conversation。

        guard 从 Conversation 定位前开始，到调用方退出上下文为止。每个 scope 同时
        只允许一个 run lease，既保护 session 生命周期，也避免两个 Agent run 并发
        修改同一 Conversation。异常和任务取消均由 finally 归还 lease。
        """
        condition = self._lifecycle_condition_for(scope)
        async with condition:
            await condition.wait_for(
                lambda: scope not in self._active_run_leases
                and scope not in self._lifecycle_transitions
            )
            self._active_run_leases.add(scope)
        try:
            yield
        finally:
            async with condition:
                self._active_run_leases.discard(scope)
                condition.notify_all()

    @asynccontextmanager
    async def run_lease(
        self, scope: ConversationScope,
    ) -> AsyncIterator[Conversation]:
        """取得 run 生命周期权并原子定位对应 Conversation。"""
        async with self.run_guard(scope):
            # 不能调用 public get_or_create：Life 路径会等待当前 lease（即自己）。
            if self.peek_cached(scope) is None:
                await self._ensure_summary_for_scope(scope)
            async with self._lock_for(scope):
                conv = await self._get_or_create_locked(scope)
            yield conv

    async def append_visible(
        self, scope: ConversationScope, message_stream_id: int, role: str,
    ) -> Conversation:
        """把一条对 message_stream 的可见引用追加进该 scope 的 Conversation。

        普通群聊旁观消息也走此路径 → 开启/延续该 scope 的活跃期，符合
        "群聊活跃期内所有消息进入 Conversation"。返回该 Conversation 以便复用。

        阶段 3b：追加前检查静默超时——超时则关闭旧活跃期再创建新活跃期。

        CA-3：静默超时需生成刚关闭 session 的摘要供新活跃期继承。生成含 LLM 调用，
        分两阶段避免锁内 LLM：锁内关闭旧活跃期 → 锁外生成其摘要 → 锁内新建继承。
        热路径（未超时）仍单临界区、无额外开销。
        """
        # 锁外预生成已有 most-recent-closed 的摘要（仅无缓存时，避免热路径多余 DB 往返）
        if self.peek_cached(scope) is None:
            await self._ensure_summary_for_scope(scope)
        async with self._lock_for(scope):
            if not await self._is_silence_expired(scope):
                conv = await self._get_or_create_locked(scope)
                await conv.append_ref(message_stream_id, role)
                return conv
        # 静默轮换会改变 session 生命周期；先释放普通 scope 锁，再等待 run lease，
        # 否则 run 内的消息送达回流可能等待同一把锁而形成死锁。拿到 transition 后
        # 二次检查，处理等待期间另一条消息已完成轮换的情况。
        async with self._lifecycle_transition(scope):
            async with self._lock_for(scope):
                if not await self._is_silence_expired(scope):
                    conv = await self._get_or_create_locked(scope)
                    await conv.append_ref(message_stream_id, role)
                    return conv
                await self._close_locked(scope)
            # 锁外生成刚关闭 session 的摘要（避免锁内 LLM），供随后新建继承；
            # transition 仍保持，故新 run 不会看到半完成的轮换。
            await self._ensure_summary_for_scope(scope)
            async with self._lock_for(scope):
                conv = await self._get_or_create_locked(scope)
                await conv.append_ref(message_stream_id, role)
                return conv

    async def close(self, scope: ConversationScope) -> None:
        """关闭该 scope 当前 active Conversation（status='closed' + 清缓存）。

        下次 get_or_create 将新建顺序 Conversation；原始消息不删除。供 3b 静默/token
        轮换与角色切换重开活跃期使用。
        """
        async with self._lifecycle_transition(scope):
            async with self._lock_for(scope):
                await self._close_locked(scope)

    async def rotate(self, scope: ConversationScope) -> Conversation:
        """关闭旧活跃期 → 携带最后一条 user ref → 创建新活跃期。

        专供 token Stage B 使用。静默轮换走 append_visible 隐式路径。
        新 Conversation 自动继承同 scope 上一段摘要（_get_or_create_locked 内惰性完成）。

        CA-3：分两阶段避免锁内 LLM——锁内关闭旧活跃期并捕获 carry-over → 锁外生成刚
        关闭 session 的摘要 → 锁内新建继承并追加 carry-over。
        CA-6：old_conv 为 None（缓存被 clear_cache 清空）时，从 DB 兜底取最后一条 user ref。

        注意：_close_locked 和 _get_or_create_locked 均不获取锁，
        从本方法（已持锁）直接调用不会触发 asyncio.Lock 重入问题。
        """
        async with self._lifecycle_transition(scope):
            async with self._lock_for(scope):
                old_conv = self._active_convs.get(scope)
                # 捕获最后一条 user ref（如有），rotate 后 carry-over 到新 conv
                if old_conv is not None:
                    ref_to_carry = self._find_last_user_ref(old_conv)
                else:
                    # CA-6: 缓存被 clear_cache 清空，从 DB 兜底取 active session 最后一条 user ref
                    ref_to_carry = await self._find_last_user_ref_from_db(scope)
                await self._close_locked(scope)
            # 保持 transition 直到摘要和新 session 均就绪，避免新 run 夹进两阶段轮换。
            await self._ensure_summary_for_scope(scope)
            async with self._lock_for(scope):
                conv = await self._get_or_create_locked(scope)
                if ref_to_carry is not None:
                    msid, role = ref_to_carry
                    await conv.append_ref(msid, role)
                return conv

    def peek_cached(self, scope: ConversationScope) -> Optional[Conversation]:
        """返回内存缓存的 Conversation（不触发创建）。供诊断/测试。"""
        return self._active_convs.get(scope)

    def clear_cache(self) -> None:
        """清空内存缓存（不改 DB 状态）。

        供角色切换等场景：下次 get_or_create 会从 DB 复用同 scope 的 active
        session 并以最新依赖（runtime/change source）重建 Conversation 对象。
        不关闭 DB session，故不丢历史。

        CA-7：递增代际号。若某 _get_or_create_locked 在本次 clear_cache 之前进入、
        之后才完成缓存写入（用旧角色依赖构建），其条目会被打上旧代际，下次访问因代际
        不匹配被拒并重建，不会跨切换存活。clear_cache 保持同步无锁（代际号使其安全）。
        """
        self._active_convs.clear()
        self._conv_versions.clear()
        self._cache_generation += 1

    # ── 内部实现 ─────────────────────────────────────────

    def _lock_for(self, scope: ConversationScope) -> asyncio.Lock:
        # asyncio 单线程：get→set 之间无 await，创建锁是原子的。
        # 锁按 scope 常驻、跨该 scope 的顺序 Conversation 复用以维持互斥；
        # 不随 close/reset/clear_cache 回收——回收一把仍有等待者的锁会破坏互斥：
        # 已取得旧锁引用的等待者与字典缺失后新建锁的后来者会在同一 scope 双临界区。
        # 增长上界＝真实群/用户去重基数，单锁内存极小，属可接受的有界常驻。
        lock = self._locks.get(scope)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[scope] = lock
        return lock

    def _lifecycle_condition_for(
        self, scope: ConversationScope,
    ) -> asyncio.Condition:
        condition = self._lifecycle_conditions.get(scope)
        if condition is None:
            condition = asyncio.Condition()
            self._lifecycle_conditions[scope] = condition
        return condition

    @asynccontextmanager
    async def _lifecycle_transition(
        self, scope: ConversationScope,
    ) -> AsyncIterator[None]:
        """阻止新 run，并等待当前 run 完成后独占生命周期变更。"""
        condition = self._lifecycle_condition_for(scope)
        async with condition:
            await condition.wait_for(
                lambda: scope not in self._lifecycle_transitions
            )
            self._lifecycle_transitions.add(scope)
            try:
                await condition.wait_for(
                    lambda: scope not in self._active_run_leases
                )
            except BaseException:
                # asynccontextmanager 尚未 yield 时 finally 不会执行；取消/异常必须在
                # 此处回滚 transition，否则该 scope 后续所有 run 都会永久等待。
                self._lifecycle_transitions.discard(scope)
                condition.notify_all()
                raise
        try:
            yield
        finally:
            async with condition:
                self._lifecycle_transitions.discard(scope)
                condition.notify_all()

    async def _get_or_create_locked(self, scope: ConversationScope) -> Conversation:
        # CA-7: 入口捕获当前代际号，缓存条目按其打标。若缓存条目代际与当前不符
        # （clear_cache 已发生），视为 miss 重建——防止旧角色依赖构建的 conv 存活。
        gen_at_entry = self._cache_generation
        cached = self._active_convs.get(scope)
        if cached is not None and self._conv_versions.get(scope) == self._cache_generation:
            if (
                scope.is_life
                and cached.id is not None
                and await self._is_daily_boundary_crossed(scope, int(cached.id))
            ):
                await self._close_locked(scope)
            else:
                return cached

        # 继承同 scope 上一段摘要：锁内只做廉价读取（生成由锁外 _ensure_summary_for_scope
        # 负责，见 CA-3），不在锁内调 LLM。
        summary_text = await self._read_inherited_summary(scope)

        conv_store = self._make_conv_store(scope)
        runtime = self._runtime_factory()
        loader = self._store.read_message_stream_batch

        sid = await self._find_active_session_id(scope)
        # R9: 跨日检测 — 进程重启后 tick_daily 未关闭旧日 session 时，
        # 在复用前关闭旧 session 并创建新 session。
        if (
            scope.is_life
            and sid is not None
            and await self._is_daily_boundary_crossed(scope, sid)
        ):
            db = self._store._persona_db
            await db.execute(
                "UPDATE persona_session SET status='closed' WHERE session_id=?",
                (sid,),
            )
            await db.commit()
            self._active_convs.pop(scope, None)
            sid = None

        if sid is not None:
            conv = await Conversation.open(
                str(sid), conv_store, runtime=runtime, stream_loader=loader,
            )
        else:
            new_sid = await self._create_session(scope)
            conv = Conversation(store=conv_store, runtime=runtime, stream_loader=loader)
            conv._id = str(new_sid)

        # 注入摘要前缀（仅在 conv 尚无消息时，摘要文本非空时）
        # 守卫用 len(conv._messages)==0 而非 sid is None，覆盖以下场景：
        # - 新建 conv（summmary 注入 → save 抛出异常 → conv 未入缓存 → 重试时
        #   _find_active_session_id 返回孤立 session, sid 非 None, 但 conv 无消息
        #   → 仍注入摘要，避免该活跃期全程缺摘要前缀）。
        # - cache-hit 路径通过上方 return 不达此处。
        if summary_text and len(conv._messages) == 0:
            conv.add_message("user", f"{NOTIFICATION_PREFIX} 之前的对话摘要：{summary_text}")
            await conv.save()

        for source in self._change_source_factory(scope):
            conv.register(source)

        self._active_convs[scope] = conv
        self._conv_versions[scope] = gen_at_entry
        return conv

    async def _close_locked(self, scope: ConversationScope) -> None:
        # 先取 conv/sid（不 pop）→ 持久化 status='closed' → 成功后再清缓存，
        # 使「持久化先于内存状态突变」：DB 写入抛异常时缓存不丢，避免异常路径下
        # 内存已丢弃而 DB 仍 active 的短暂不一致。_locks 不回收（见 _lock_for）。
        conv = self._active_convs.get(scope)
        sid = None
        if conv is not None and conv.id is not None:
            try:
                sid = int(conv.id)
            except (ValueError, TypeError):
                sid = None
        if sid is None:
            sid = await self._find_active_session_id(scope)
        if sid is None:
            self._active_convs.pop(scope, None)
            return
        db = self._store._persona_db
        await db.execute(
            "UPDATE persona_session SET status='closed' WHERE session_id=?",
            (sid,),
        )
        await db.commit()
        self._active_convs.pop(scope, None)
        # R12: 通知外部（如 Orchestrator）清理 scope 相关缓存
        if self._on_scope_closed is not None:
            try:
                maybe_coro = self._on_scope_closed(scope)
                if maybe_coro is not None:
                    await maybe_coro
            except Exception:
                logger.warning(
                    "ConversationRegistry._close_locked: on_scope_closed 回调失败",
                    exc_info=True,
                )

    def _make_conv_store(self, scope: ConversationScope) -> ConversationStore:
        # 群 scope 的 session 不绑定单一 user；私聊 scope 的 key 即 user_id。
        user_id = scope.key if scope.is_private else ""
        return ConversationStore(
            self._store,
            user_id=user_id,
            character_id=self._character_id_provider(),
            scope_namespace=scope.namespace,
            scope_key=scope.key,
        )

    async def _find_active_session_id(self, scope: ConversationScope) -> Optional[int]:
        db = self._store._persona_db
        cursor = await db.execute(
            "SELECT session_id FROM persona_session "
            "WHERE status='active' AND scope_namespace=? AND scope_key=? "
            "ORDER BY session_id DESC LIMIT 1",
            (scope.namespace, scope.key),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return int(_cell(row, 0, "session_id"))

    async def _is_daily_boundary_crossed(
        self, scope: ConversationScope, session_id: int,
    ) -> bool:
        """检查指定 session 的 last_active_at 日期是否与当前虚拟日不同。

        进程重启后 tick_daily 未执行时，旧日 session 的 last_active_at 日期与
        当前 Clock 的日期不同，用于补偿性轮换——关闭旧 session 并创建新 session。
        """
        import datetime
        db = self._store._persona_db
        cursor = await db.execute(
            "SELECT last_active_at FROM persona_session WHERE session_id=?",
            (session_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return False
        last_active_str = str(_cell(row, 0, "last_active_at") or "")
        if not last_active_str:
            return False
        try:
            last_date = datetime.datetime.fromisoformat(last_active_str).date()
        except ValueError:
            return False
        now = get_clock().now()
        return last_date != now.date()

    @staticmethod
    def _find_last_user_ref(conv: Conversation) -> Optional[tuple[int, str]]:
        """从内存 conv._messages 取最后一条 user ref 的 (message_stream_id, role)。"""
        for msg in reversed(conv._messages):
            if msg.get("entry_type") == "ref" and msg.get("role") == "user":
                msid = msg.get("message_stream_id")
                if msid is not None:
                    return (int(msid), str(msg["role"]))
        return None

    async def _find_last_user_ref_from_db(
        self, scope: ConversationScope,
    ) -> Optional[tuple[int, str]]:
        """CA-6：从 DB 取同 scope active session 最后一条 user ref。

        供 rotate 在 old_conv 缓存缺失（clear_cache 清空）时兜底 carry-over。纯 SELECT。
        """
        sid = await self._find_active_session_id(scope)
        if sid is None:
            return None
        db = self._store._persona_db
        cursor = await db.execute(
            "SELECT message_stream_id FROM persona_session_message "
            "WHERE session_id=? AND entry_type='ref' AND role='user' "
            "AND message_stream_id IS NOT NULL "
            "ORDER BY sequence DESC LIMIT 1",
            (sid,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        msid = _cell(row, 0, "message_stream_id")
        if msid is None:
            return None
        return (int(msid), "user")

    async def _create_session(self, scope: ConversationScope) -> int:
        db = self._store._persona_db
        user_id = scope.key if scope.is_private else ""
        if scope.is_life:
            now = get_clock().now().isoformat(sep=" ")
            cursor = await db.execute(
                "INSERT INTO persona_session "
                "(user_id, character_id, status, scope_namespace, scope_key, "
                " last_active_at, created_at) "
                "VALUES (?, ?, 'active', ?, ?, ?, ?)",
                (user_id, self._character_id_provider(), scope.namespace, scope.key, now, now),
            )
        else:
            cursor = await db.execute(
                "INSERT INTO persona_session "
                "(user_id, character_id, status, scope_namespace, scope_key) "
                "VALUES (?, ?, 'active', ?, ?)",
                (user_id, self._character_id_provider(), scope.namespace, scope.key),
            )
        await db.commit()
        return int(cursor.lastrowid)

    # ── 阶段 3b：静默检查与摘要 ───────────────────────────────

    async def _is_silence_expired(self, scope: ConversationScope) -> bool:
        """读 DB last_active_at，判断同 scope 活跃会话是否已超静默阈值。

        "现在"的基准必须与写入侧一致，在 Python 侧取数比较（不用 SQL 'now'）：
        - life scope 的 last_active_at 由生产 Clock 写入（上海 naive，
          见 _create_session / ConversationStore），"现在"取同一 Clock；
        - chat scope 的 last_active_at 由 SQLite CURRENT_TIMESTAMP 写入（UTC），
          "现在"取 UTC naive。

        无活跃会话或 last_active_at 缺失/不可解析时返回 False（不触发轮换）。

        配置假设：silence_seconds 应为正的常规值（生产群 1800s / 私聊 86400s）。
        gap 取 0 这类极端值会把刚创建 session 的微小时差判为超时并触发级联轮换；
        这不是生产配置，不额外防御。
        """
        import datetime
        db = self._store._persona_db
        gap = self._private_silence_seconds if scope.is_private else self._group_silence_seconds
        cursor = await db.execute(
            "SELECT last_active_at FROM persona_session "
            "WHERE status='active' AND scope_namespace=? AND scope_key=? "
            "ORDER BY session_id DESC LIMIT 1",
            (scope.namespace, scope.key),
        )
        row = await cursor.fetchone()
        if row is None:
            return False
        last_active_str = str(_cell(row, 0, "last_active_at") or "")
        if not last_active_str:
            return False
        try:
            last_active = datetime.datetime.fromisoformat(last_active_str)
        except ValueError:
            return False
        if scope.is_life:
            now = get_clock().now()
        else:
            now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
        return (now - last_active).total_seconds() > gap

    async def _read_inherited_summary(self, scope: ConversationScope) -> str:
        """读取同 scope 紧邻上一段 closed session 的摘要（只读，不生成、不调 LLM）。

        供 _get_or_create_locked 在锁内注入。生成由锁外 _ensure_summary_for_scope 负责
        （CA-3）；两阶段路径（silence/rotate）已在锁外先生成刚关闭 session 的摘要，故此处
        读到的即为其摘要。

        R7(b): 只继承紧邻上一段。若上一段无摘要（消息不足未生成或生成失败），返回空字符串，
        不跨段回退到更旧 session 的摘要——由 history 工具补查而非自动注入陈旧上下文。
        """
        db = self._store._persona_db
        cursor = await db.execute(
            "SELECT session_id, summary_text FROM persona_session "
            "WHERE status='closed' AND scope_namespace=? AND scope_key=? "
            "ORDER BY session_id DESC LIMIT 1",
            (scope.namespace, scope.key),
        )
        row = await cursor.fetchone()
        if row is None:
            return ""
        summary_text = str(_cell(row, 1, "summary_text") or "")
        return summary_text

    async def _find_older_summary(self, scope: ConversationScope,
                                   newer_than_sid: int) -> str:
        """回退查找比 newer_than_sid 更旧的、有 summary_text 的 Session。

        当最近 closed session 消息数不足以生成摘要时，回退继承更旧 session 的摘要。
        无匹配时返回 ''。
        """
        db = self._store._persona_db
        cursor = await db.execute(
            "SELECT summary_text FROM persona_session "
            "WHERE status='closed' AND scope_namespace=? AND scope_key=? "
            "AND summary_text != '' AND session_id < ? "
            "ORDER BY session_id DESC LIMIT 1",
            (scope.namespace, scope.key, newer_than_sid),
        )
        row = await cursor.fetchone()
        if row is None:
            return ""
        return str(_cell(row, 0, "summary_text"))

    async def _ensure_summary_for_scope(self, scope: ConversationScope) -> str:
        """确保同 scope 最近 closed session 有可用摘要（必要时生成并落库）。

        CA-3：本方法可能触发 LLM 调用（Step 7），必须在 per-scope 锁**外**调用，避免
        锁内最长 30s 的 LLM 阻塞同 scope 所有操作。返回值供诊断，锁内注入改由
        _read_inherited_summary 只读获取。

        执行顺序：
        1. 找最近 closed session（不论有无摘要）。
        2. 若有非空摘要 → 直接返回。
        3. 若无摘要且消息数 >= SUMMARY_MIN_MESSAGES → 加载消息（5）→双检（5.5）→
           生成摘要（7）并写回返回（8）。
        4. 若无摘要且消息数 < SUMMARY_MIN_MESSAGES → 回退更旧 session 的摘要。
        5. 仍无 → 返回 ''。

        Step 5.5 双检：在生成前再查一次 DB，减小但不消除锁外并发导致的重复 LLM 调用窗口。
        摘要失败绝不阻断调用方，绝不抛异常。
        """
        db = self._store._persona_db

        # Step 1: 找最近 closed session（不论 summary_text）
        cursor = await db.execute(
            "SELECT session_id, summary_text FROM persona_session "
            "WHERE status='closed' AND scope_namespace=? AND scope_key=? "
            "ORDER BY session_id DESC LIMIT 1",
            (scope.namespace, scope.key),
        )
        row = await cursor.fetchone()
        if row is None:
            return ""

        old_sid = int(_cell(row, 0, "session_id"))
        summary_text = str(_cell(row, 1, "summary_text") or "")

        # Step 2: 已有摘要 → 直接复用
        if summary_text:
            return summary_text

        # 无 summarizer → 无法为最近 session 生成摘要，回退更旧
        if self._summarizer is None:
            return await self._find_older_summary(scope, old_sid)

        # Step 3: 检查消息数
        count_cursor = await db.execute(
            "SELECT COUNT(*) as cnt FROM persona_session_message WHERE session_id=? "
            "AND (entry_type='ref' OR entry_type='own' AND content NOT LIKE ?)",
            (old_sid, f"{NOTIFICATION_PREFIX}%"),
        )
        count_row = await count_cursor.fetchone()
        msg_count = _cell(count_row, 0, "cnt") or 0
        if msg_count < SUMMARY_MIN_MESSAGES:
            # Step 4: 消息不足 → 回退更旧 session 的有摘要记录
            return await self._find_older_summary(scope, old_sid)

        # Step 5: 加载消息并解析内容
        msg_cursor = await db.execute(
            "SELECT role, content, tool_calls, tool_call_id, name, "
            "provider_context, message_stream_id, entry_type "
            "FROM persona_session_message WHERE session_id=? ORDER BY sequence",
            (old_sid,),
        )
        msg_rows = await msg_cursor.fetchall()

        # 构建消息列表，解析 ref 条目引用 message_stream 正文
        messages = [_recompose_message(row) for row in msg_rows]
        ref_ids: list[int] = []
        for message in messages:
            if message.get("entry_type") == "ref":
                msid_raw = message.get("message_stream_id")
                if msid_raw is not None:
                    ref_ids.append(int(msid_raw))
                message["content"] = ""

        # 从 message_stream 加载 ref 内容（W2: 缺失记录统一 DANGLING_REF_FALLBACK）
        if ref_ids:
            try:
                loaded = await self._store.read_message_stream_batch(ref_ids)
            except Exception:
                logger.warning(
                    "ConversationRegistry._ensure_summary_for_scope: "
                    "read_message_stream_batch 失败", exc_info=True,
                )
                loaded = {}
            for m in messages:
                if m.get("entry_type") == "ref":
                    msid = m.get("message_stream_id")
                    record = loaded.get(msid) if msid is not None else None
                    if record is not None:
                        m["content"] = getattr(record, "content", "") or DANGLING_REF_FALLBACK
                    else:
                        m["content"] = DANGLING_REF_FALLBACK

        # Step 5.5: 双检 — 生成前再查一次 DB。若锁外并发调用（另一路 CA-3 路径）
        # 刚把摘要写入（Steps 1-5 期间另一线程完成了 Step 7→8），直接返回摘要跳过生成。
        # 仍有极窄窗口：两路并发同时通过此检查则各自生成一次，最终状态一致。
        cursor_dup = await db.execute(
            "SELECT session_id, summary_text FROM persona_session "
            "WHERE status='closed' AND scope_namespace=? AND scope_key=? "
            "ORDER BY session_id DESC LIMIT 1",
            (scope.namespace, scope.key),
        )
        row_dup = await cursor_dup.fetchone()
        if row_dup:
            existing = str(_cell(row_dup, 1, "summary_text") or "")
            if existing:
                return existing

        # Step 7: 生成摘要（双检已过，确认需生成）
        try:
            summary_text = await self._summarizer.generate_summary(messages)
        except Exception:
            logger.warning(
                "ConversationRegistry._ensure_summary_for_scope: "
                "摘要生成失败（不阻断）", exc_info=True,
            )
            return ""

        if not summary_text:
            return ""

        # Step 8: CAS 写入 DB（compare-and-set，防止并发覆盖）
        # 两个 summarizer 同时通过双检后各自生成摘要，只有第一个 CAS 成功的写入生效；
        # 竞争失败者回读胜者的摘要文本并返回，保证不可变性。
        try:
            result = await db.execute(
                "UPDATE persona_session SET summary_text=? "
                "WHERE session_id=? AND summary_text=''",
                (summary_text, old_sid),
            )
            await db.commit()
            if result.rowcount == 0:
                # CAS 失败：并发写入者先到达，读取胜者摘要
                read_cursor = await db.execute(
                    "SELECT summary_text FROM persona_session WHERE session_id=?",
                    (old_sid,),
                )
                winner = await read_cursor.fetchone()
                if winner:
                    return str(_cell(winner, 0, "summary_text") or summary_text)
                return summary_text
            return summary_text
        except Exception:
            logger.warning(
                "ConversationRegistry._ensure_summary_for_scope: "
                "摘要写入 DB 失败（不阻断）", exc_info=True,
            )
            return ""
