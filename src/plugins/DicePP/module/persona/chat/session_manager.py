"""SessionManager — 对话 session 生命周期管理

负责 session 的获取/创建、消息追加、token 估算更新、删除、压缩、回复后处理。
"""
import asyncio
from dataclasses import dataclass, field
from datetime import datetime, date, timezone as tz
from typing import Dict, List, Optional, TYPE_CHECKING

from utils.logger import logger

if TYPE_CHECKING:
    from ..data.store import PersonaDataStore
    from ..chat.chat_config import ChatConfig

# 压缩摘要 system prompt
COMPRESSION_SYSTEM_PROMPT = """\
你是一个对话摘要助手。请用一段简短的中文总结以下角色扮演对话的关键信息。

要求：
- 只记录明确发生的内容，不要推断或编造
- 保留用户提到的重要事项、角色做出的承诺、达成的共识
- 保留准确的名称、数字、时间等具体信息
- 保留用户和角色的情绪状态变化
- 用 200-300 字概括"""


_INFINITE_GAP_SECONDS = 10 ** 6


def _now() -> datetime:
    return datetime.now(tz.utc)


def _make_time_notification(timezone_name: str = "Asia/Shanghai") -> str:
    """生成时间通知消息。"""
    from utils.time import wall_now as _wall_now
    local_dt = _wall_now(timezone_name)
    weekdays = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    wd = weekdays[local_dt.weekday()]
    return f"[通知] 现在是{local_dt.year}年{local_dt.month}月{local_dt.day}日，{wd}。"


@dataclass
class SessionDecision:
    """get_or_create 的返回结构"""
    session: "PersonaSession"
    is_new: bool
    static_rebuilt: bool = False
    notifications: List[str] = field(default_factory=list)


class SessionManager:
    """会话管理器 — 负责 session 生命周期、并发安全、动态信息追踪"""

    def __init__(
        self,
        store: "PersonaDataStore",
        config: "ChatConfig",
        timezone: str = "Asia/Shanghai",
    ):
        self._store = store
        self._config = config
        self._timezone = timezone
        self._locks: Dict[int, "asyncio.Lock"] = {}
        self._locks_guard: Optional["asyncio.Lock"] = None  # type: ignore[assignment]

        # 动态信息追踪（按 user_id 独立）
        self._trackers: Dict[str, dict] = {}
        # 后台任务引用（防止 GC 回收未完成的 ensure_future Task）
        self._bg_tasks: "set[asyncio.Task]" = set()

    def get_tracker(self, user_id: str) -> dict:
        if user_id not in self._trackers:
            self._trackers[user_id] = {
                "last_warmth_label": None,
                "last_event_notification_date": None,
                "activated_lore_keys": set(),
                "last_profile_hash": None,
                "seen_speakers": set(),
                "notified_event_ids": set(),
            }
        return self._trackers[user_id]

    def get_warmth_label(self, user_id: str) -> Optional[str]:
        return self.get_tracker(user_id).get("last_warmth_label")

    def set_warmth_label(self, user_id: str, label: str) -> None:
        self.get_tracker(user_id)["last_warmth_label"] = label

    def _clear_tracker(self, user_id: str) -> None:
        self._trackers.pop(user_id, None)

    async def _get_lock_guard(self) -> "asyncio.Lock":
        if self._locks_guard is None:
            self._locks_guard = asyncio.Lock()
        return self._locks_guard

    async def _get_lock(self, session_id: int) -> "asyncio.Lock":
        guard = await self._get_lock_guard()
        async with guard:
            if session_id not in self._locks:
                self._locks[session_id] = asyncio.Lock()
            return self._locks[session_id]

    @staticmethod
    def compute_static_hash(static_prompt: str) -> str:
        import hashlib
        return hashlib.md5(static_prompt.encode()).hexdigest()[:16]

    async def get_or_create(
        self,
        user_id: str,
        character_id: str,
        static_prompt: str,
        static_hash: str,
        is_group: bool = False,
    ) -> SessionDecision:
        from ..data.models import PersonaSession

        gap_seconds = (
            self._config.group_session_gap_seconds if is_group
            else self._config.private_session_gap_seconds
        )
        token_budget = (
            self._config.group_session_token_budget if is_group
            else self._config.private_session_token_budget
        )

        active = await self._store.get_active_session(user_id)
        now = _now()

        # 无活跃 session → 冷启动
        if active is None:
            self._clear_tracker(user_id)
            session = await self._store.create_session(
                user_id=user_id,
                character_id=character_id,
                static_prompt=static_prompt,
                static_hash=static_hash,
                token_budget=token_budget,
                status="active",
                last_active_at=now,
            )
            time_note = _make_time_notification(self._timezone)
            return SessionDecision(
                session=session, is_new=True,
                notifications=[time_note],
            )

        # gap 超时 → 归档旧 + 建新
        gap = (now - active.last_active_at).total_seconds() if active.last_active_at else _INFINITE_GAP_SECONDS
        if gap > gap_seconds:
            await self.compact_session(active.session_id, reason="gap_expired")
            session = await self._store.create_session(
                user_id=user_id,
                character_id=character_id,
                static_prompt=static_prompt,
                static_hash=static_hash,
                token_budget=token_budget,
                status="active",
                last_active_at=now,
            )
            time_note = _make_time_notification(self._timezone)
            return SessionDecision(
                session=session, is_new=True,
                notifications=[time_note],
            )

        # static_hash 变化 → 重建静态基座
        if active.static_hash != static_hash:
            await self._store.update_session(
                active.session_id,
                static_prompt=static_prompt,
                static_hash=static_hash,
                last_active_at=now,
            )
            active.static_prompt = static_prompt
            active.static_hash = static_hash
            return SessionDecision(
                session=active, is_new=False, static_rebuilt=True,
            )

        # 正常复用
        await self._store.update_session(active.session_id, last_active_at=now)
        return SessionDecision(session=active, is_new=False)

    async def append_messages(
        self, session_id: int, messages: List["PersonaSessionMessage"],
    ) -> None:
        lock = await self._get_lock(session_id)
        async with lock:
            await self._store.add_session_messages(session_id, messages)

    async def update_token_estimate(self, session_id: int, estimate: int) -> None:
        lock = await self._get_lock(session_id)
        async with lock:
            await self._store.update_session(session_id, token_estimate=estimate)

    async def delete_session(self, user_id: str) -> None:
        active = await self._store.get_active_session(user_id)
        if active:
            lock = await self._get_lock(active.session_id)
            async with lock:
                await self._store.delete_session(active.session_id)
            self._clear_tracker(user_id)

    async def compact_session(
        self,
        session_id: int,
        router=None,
        reason: str = "token_threshold",
    ) -> tuple:
        """压缩一个 session：摘要 old + 保留 recent → 创建新 session。

        三阶段：锁内读取+切分 → 锁外 LLM 摘要 → 锁内写入+归档。
        避免在持有 per-session 锁期间阻塞等待 LLM 响应。

        Returns:
            (success, summary_text_or_none)
        """
        import time
        from ..data.models import PersonaSessionMessage
        from .compression import (
            ensure_tool_pairs, estimate_session_tokens, KEEP_RECENT,
            _get_msg_attr,
        )

        t0 = time.monotonic()

        # ── Phase 1: 锁内 → 读取 + 切分 ──
        lock = await self._get_lock(session_id)
        async with lock:
            session = await self._store.get_session_by_id(session_id)
            if session is None:
                return (False, None)

            all_msgs = await self._store.get_session_messages(session_id)
            msg_count = len(all_msgs)

            # 不足 3 轮（6 条）→ 直接删除
            if msg_count < 6:
                await self._store.delete_session(session_id)
                self._clear_tracker(session.user_id)
                logger.info(
                    "[SessionCompress] session_id=%d user_id=%s "
                    "reason=%s action=skip_delete msg_count=%d",
                    session_id, session.user_id, reason, msg_count,
                )
                return (False, None)

            # 切分 old / recent
            old_msgs, recent_msgs = ensure_tool_pairs(all_msgs, KEEP_RECENT)
            token_before = estimate_session_tokens(all_msgs)

            # 保存写阶段需要的字段（锁外不持有 session 引用）
            _user_id = session.user_id
            _character_id = session.character_id
            _static_prompt = session.static_prompt
            _static_hash = session.static_hash
            _token_budget = session.token_budget

        # ── Phase 2: 锁外 → LLM 摘要 ──
        summary_text = None
        fallback = "no_router"
        if router is not None:
            try:
                summary_text = await _llm_summarize(router, old_msgs)
                fallback = ""
            except Exception as e:
                logger.warning(
                    "[SessionCompress] LLM summary failed: %s", e
                )
                fallback = f"llm_error:{type(e).__name__}"

        # 兜底硬截断
        if summary_text is None:
            summary_text = (
                "[通知] 之前的对话内容超出上下文限制，"
                "部分历史已丢弃。"
            )
            if not fallback:
                fallback = "hard_truncation"

        # 生成摘要文本（带 [通知] 前缀）
        notification_text = (
            f"[通知] 之前的对话摘要：{summary_text}"
            if not summary_text.startswith("[通知]")
            else summary_text
        )

        # ── Phase 3: 锁内 → 验证 + 写入 + 归档 ──
        lock = await self._get_lock(session_id)
        async with lock:
            # 验证 session 未被并发修改
            session_check = await self._store.get_session_by_id(session_id)
            if session_check is None or session_check.status != "active":
                logger.info(
                    "[SessionCompress] session_id=%d user_id=%s "
                    "reason=%s action=skip_concurrent session_already_handled",
                    session_id, _user_id, reason,
                )
                return (False, None)

            # 创建新 session
            new_session = await self._store.create_session(
                user_id=_user_id,
                character_id=_character_id,
                static_prompt=_static_prompt,
                static_hash=_static_hash,
                token_budget=_token_budget,
                status="active",
                last_active_at=_now(),
            )

            # 注入摘要 + 追加 recent
            # sequence 由 add_session_messages 自动递增分配
            summary_msg = PersonaSessionMessage(
                session_id=new_session.session_id,
                role="user",
                content=notification_text,
            )
            for i, msg in enumerate(recent_msgs):
                if isinstance(msg, dict):
                    msg["session_id"] = new_session.session_id
                else:
                    msg.session_id = new_session.session_id
            await self._store.add_session_messages(
                new_session.session_id, [summary_msg] + recent_msgs,
            )

            # 归档旧 session
            await self._store.update_session(session_id, status="archived")

            # 清空追踪状态
            self._clear_tracker(_user_id)

            # 计算新 token 估算
            new_msgs = await self._store.get_session_messages(new_session.session_id)
            token_after = estimate_session_tokens(new_msgs)
            await self._store.update_session(
                new_session.session_id, token_estimate=token_after,
            )

            duration_ms = int((time.monotonic() - t0) * 1000)
            logger.info(
                "[SessionCompress] session_id=%d->%d user_id=%s "
                "reason=%s trigger=token_estimate:%d>=threshold:%d "
                "messages_before=%d messages_summarized=%d messages_kept=%d "
                "token_before=%d token_after=%d duration_ms=%d "
                "summary_chars=%d fallback=%s",
                session_id, new_session.session_id, _user_id,
                reason,
                token_before, int(_token_budget * 0.9),
                msg_count, len(old_msgs), len(recent_msgs),
                token_before, token_after, duration_ms,
                len(summary_text) if summary_text else 0,
                fallback,
            )

            return (True, notification_text)

    async def add_bypass_message(self, session_id: int, content: str) -> None:
        """群聊旁路：封装 PersonaSessionMessage 构造并由锁保护写入。"""
        from ..data.models import PersonaSessionMessage
        msg = PersonaSessionMessage(
            session_id=session_id,
            role="user",
            content=content,
        )
        await self.append_messages(session_id, [msg])

    # ── 回复后处理 ──────────────────────────────────────────────

    async def on_chat_complete(
        self, user_id: str, group_id: str, response: str, user_msg: str,
        router=None,
    ) -> None:
        """LLM 回复后的 session 更新（追加消息 + token 估算 + 压缩 + 异步刷流）。"""
        if not response:
            return
        try:
            from ..data.models import PersonaSessionMessage
            from ..chat.compression import estimate_session_tokens, should_compress

            scope_id = group_id or user_id
            active = await self._store.get_active_session(scope_id)
            if not active:
                return

            # 追加 assistant 消息
            assistant_msg = PersonaSessionMessage(
                session_id=active.session_id,
                role="assistant",
                content=response,
            )
            await self.append_messages(active.session_id, [assistant_msg])

            # 更新 token 估算
            all_msgs = await self._store.get_session_messages(active.session_id)
            est = estimate_session_tokens(all_msgs)
            await self.update_token_estimate(active.session_id, est)

            # 异步压缩检查
            if should_compress(est, active.token_budget):
                task = asyncio.ensure_future(
                    self.compact_session(
                        active.session_id, router=router,
                        reason="token_threshold",
                    )
                )
                self._bg_tasks.add(task)
                task.add_done_callback(self._bg_tasks.discard)

            # 异步入 message_stream（仅 assistant 最终回复，user 消息由 _inbound_message_recorder 负责写入）
            task = asyncio.ensure_future(
                self._flush_to_message_stream(user_id, group_id, response)
            )
            self._bg_tasks.add(task)
            task.add_done_callback(self._bg_tasks.discard)
        except Exception as e:
            logger.warning(f"[Session] 后处理失败: {e}")

    async def _flush_to_message_stream(
        self, user_id: str, group_id: str, assistant_text: str,
    ) -> None:
        """异步写入 message_stream（仅 assistant 最终回复，user 消息由 _inbound_message_recorder 负责写入）。"""
        try:
            if assistant_text:
                from ..data.models import MessageType
                await self._store.add_message_stream(
                    user_id=user_id,
                    group_id=group_id,
                    role="assistant",
                    type=MessageType.CHAT,
                    content=assistant_text,
                )
        except Exception as e:
            logger.warning(f"[Session] message_stream 异步写入失败: {e}")

    async def shutdown(self) -> None:
        """取消所有未完成的 background task 并等待清理。"""
        for task in list(self._bg_tasks):
            if not task.done():
                task.cancel()
        if self._bg_tasks:
            await asyncio.gather(*self._bg_tasks, return_exceptions=True)


async def _llm_summarize(
    router, old_msgs: List,
) -> Optional[str]:
    """调用 LLM 对旧消息生成摘要。"""
    from .compression import _get_msg_attr

    lines = []
    for msg in old_msgs:
        role = _get_msg_attr(msg, "role")
        content = _get_msg_attr(msg, "content")
        if role == "user":
            lines.append(f"用户：{content}")
        elif role == "assistant":
            lines.append(f"角色：{content}")

    if not lines:
        return None

    conversation_text = "\n".join(lines)
    if not conversation_text.strip():
        return None

    messages = [
        {"role": "system", "content": COMPRESSION_SYSTEM_PROMPT},
        {"role": "user", "content": f"对话记录：\n{conversation_text}"},
    ]

    resp = await router.generate(
        messages=messages,
        temperature=0.3,
        timeout=30,
    )
    return resp.content.strip() if resp and resp.content else None
