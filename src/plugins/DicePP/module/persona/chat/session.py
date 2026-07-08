"""对话会话管理器

负责构造 LLM 上下文、调用 router、处理工具回调、执行评分。
通过依赖注入接收 store/router 等，零外部 import。
T6: 已废弃 — chat() 方法抛出 NotImplementedError，请使用 ChatOrchestrator。
"""
from typing import List, Dict, Optional, Any, Tuple, TYPE_CHECKING
import asyncio
import json
import time
import random
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone as tz

from utils.string import estimate_tokens
from utils.logger import logger
from utils.time import wall_now

from ..data.store import PersonaDataStore
from ..data.models import (
    RelationshipState,
    UnifiedMessage,
    MessageType,
)
from ..llm.router import LLMRouter, QuotaExceeded
from ..character.models import Character
from ..chat.context import ContextBuilder
from ..chat.chat_config import ChatConfig
from ..game.decay import DecayCalculator
from utils.time import wall_now, DEFAULT_EPOCH, format_timestamp, format_relative_time
from ..life.protocols import SleepGate
from ..llm.coordinator import LLMCallCoordinator
from ..gateway.port import MessagePort
from ..data.models import PersonaSessionMessage
from ..chat.compression import estimate_session_tokens, should_compress
from ..chat.session_manager import _make_time_notification

_DEFAULT_SLEEP_MESSAGES = ("角色正在休息，请稍后再来",)

if TYPE_CHECKING:
    from .scoring_trigger import ScoringTrigger
    from .response_handler import ResponseHandler
    from .session_manager import SessionManager


def _format_group_message(msg_dict: Dict[str, Any], ts: str, img_prefix: str, content: str) -> str:
    """群聊消息格式化（[HH:MM] [speaker_name] content + 图片标记）。

    session.py 和 command.py 共用此函数。
    """
    speaker = msg_dict.get("speaker_name") or msg_dict.get("display_name", "")
    speaker_part = f"[{speaker}] " if speaker else ""
    return f"[{ts}] {speaker_part}{img_prefix}{content}"


@dataclass
class ChatCallContext:
    """chat() 调用上下文 — 收敛透传参数，减少多层签名变更。

    替代原先分散在 4 层调用链中的 is_command / image_data_urls /
    transient_message / nickname 四个独立参数。
    """

    is_command: bool = False
    image_data_urls: Optional[List[str]] = None
    transient_message: Optional[str] = None
    nickname: str = ""


class ChatSession:
    """对话会话管理器 — 负责对话编排、门控、上下文构建、工具调用委托"""

    def __init__(
        self,
        store: PersonaDataStore,
        router: LLMRouter,
        coordinator: LLMCallCoordinator,
        character: Character,
        config: ChatConfig,
        scoring_trigger: "ScoringTrigger",
        response_handler: "ResponseHandler",
        context_builder: ContextBuilder,
        decay_calculator: Optional[DecayCalculator] = None,
        query_store: Any = None,
        resolve_db: Any = None,
        sleep_gate: Optional[SleepGate] = None,
        session_manager: Optional["SessionManager"] = None,
    ):
        # session_manager 通过 factory 在构造后立即注入（非构造时传入），
        # _build_messages 开头保留 `if self.session_manager is None` 防御检查。
        self.store = store
        self.router = router
        self.coordinator = coordinator
        self.character = character
        self.config = config
        self._scoring_trigger = scoring_trigger
        self._response_handler = response_handler
        self.context_builder = context_builder
        self.decay_calculator = decay_calculator
        self.query_store = query_store
        self.resolve_db = resolve_db
        self._sleep_gate = sleep_gate
        self.session_manager = session_manager
        self._last_messages: Dict[str, Tuple[str, float]] = {}
        # 动态信息追踪统一由 session_manager 管理（get_relation_label / set_relation_label）

    def update_character(self, character: Character) -> None:
        """同步新的角色卡引用"""
        self.character = character
        self.context_builder.update_character(character)
        self._scoring_trigger.update_character(character)

    # ── 公开 API ──────────────────────────────────────────────

    async def is_awake(self) -> bool:
        """角色是否处于唤醒状态。

        Gate 为 None 时默认返回 True。
        """
        if self._sleep_gate is not None:
            return await self._sleep_gate.is_awake()
        return True

    async def chat(
        self,
        user_id: str,
        group_id: str,
        message: str,
        ctx: Optional[ChatCallContext] = None,
    ) -> Optional[str]:
        """处理单条用户消息，返回回复文本（None 表示不回复）

        Args:
            ctx: 调用上下文（is_command / image_data_urls / transient_message / nickname）。
                 为 None 时使用默认值（非命令模式，无图片，无临时消息，空昵称）。
        """
        if ctx is None:
            ctx = ChatCallContext()
        is_command = ctx.is_command
        image_data_urls = ctx.image_data_urls
        # 5 秒内完全相同的消息去重（防手抖/网络重试）
        dedup_key = f"{user_id}:{group_id}"
        now = time.monotonic()
        last = self._last_messages.get(dedup_key)
        if last and last[0] == message and (now - last[1]) < 5.0:
            logger.debug(f"去重: 5秒内重复消息已忽略 user={user_id}")
            return None
        self._last_messages[dedup_key] = (message, now)
        # 清理超过 60s 的旧去重条目
        expired = [k for k, v in self._last_messages.items() if now - v[1] > 60]
        for k in expired:
            self._last_messages.pop(k, None)

        # ChatSession.chat() 已废弃 — 请使用 ChatOrchestrator.chat()
        raise NotImplementedError(
            "ChatSession.chat() 已废弃，请使用 ChatOrchestrator.chat()"
        )

    async def clear_history(self, user_id: str, group_id: str) -> None:
        """清空对话历史（message_stream + session）"""
        await self.store.clear_messages(user_id, group_id)
        if self.session_manager:
            scope_id = group_id or user_id
            await self.session_manager.delete_session(scope_id)

    # ── 回复后处理 ──────────────────────────────────────────────

    async def _after_response(
        self, user_id: str, group_id: str, user_msg: str, assistant_msg: str
    ) -> None:
        """回复后处理聚合点：委托给 scoring_trigger.on_interaction

        调用时序：在持久化完成之后、方法返回之前。
        调用点：_coordinator_chat_call_fn 和 _coordinator_on_exhausted。
        """
        await self._scoring_trigger.on_interaction(user_id, group_id, user_msg, assistant_msg)
    # ── 历史管理 ──────────────────────────────────────────────

    async def _fetch_short_term_history(
        self,
        user_id: str,
        group_id: str,
        limit: Optional[int] = None,
    ) -> Tuple[List[Dict[str, str]], bool]:
        """获取并格式化近期对话历史

        Returns: (history_dicts, was_truncated)
        """
        if limit is None:
            limit = self.config.max_messages
        if not group_id:
            history = await self.store.get_recent_messages(user_id, group_id="", limit=limit)
            history_dicts = [
                {
                    "role": msg.role,
                    "content": msg.content,
                    "speaker_name": "你" if msg.role == "user" else "我",
                    "created_at": msg.created_at,
                    "agent_run_id": msg.agent_run_id,
                    "interaction_id": msg.interaction_id,
                    "segment_index": msg.segment_index,
                    "segment_phase": msg.segment_phase,
                    "image_meta": msg.image_meta,
                }
                for msg in history
            ]
            return history_dicts, False

        history = await self.store.get_group_messages(group_id, limit=None)
        return self._apply_token_window(history)

    def _apply_token_window(
        self,
        history: List[UnifiedMessage],
    ) -> Tuple[List[Dict[str, str]], bool]:
        """群聊 Token-based 动态窗口（从新到旧收集，升序输出）"""
        if not history:
            return [], False

        original_count = len(history)

        now = wall_now(self.config.timezone)
        max_age = timedelta(minutes=self.config.group_max_age_minutes)
        budget = self.config.group_context_budget_tokens
        max_msgs = self.config.group_max_messages
        single_max = self.config.group_single_message_max_tokens

        result: List[Dict[str, str]] = []
        total_tokens = 0.0

        for msg in reversed(history):
            if len(result) >= max_msgs:
                break

            if msg.created_at and (now - msg.created_at) > max_age:
                break

            content = msg.content
            if not content:
                continue

            content_tokens = estimate_tokens(content)
            if content_tokens > single_max:
                ratio = single_max / content_tokens
                max_chars = max(1, int(len(content) * ratio))
                content = content[:max_chars]
                while len(content) > 1 and estimate_tokens(content) > single_max:
                    content = content[:-1]

            speaker_name = "我" if msg.role == "assistant" else (msg.display_name or "群友")
            ts = format_timestamp(msg.created_at, now)
            rel = format_relative_time(msg.created_at, now)
            extra = f" {rel}" if rel else ""
            ts_prefix = f"[{ts}{extra}] " if ts else ""
            formatted = f"{ts_prefix}[{speaker_name}] {content}"
            msg_cost = estimate_tokens(formatted)

            if total_tokens + msg_cost > budget and result:
                break

            result.insert(0, {
                "role": msg.role,
                "content": content,
                "speaker_name": speaker_name,
                "created_at": msg.created_at,
                "agent_run_id": msg.agent_run_id,
                "interaction_id": msg.interaction_id,
                "segment_index": msg.segment_index,
                "segment_phase": msg.segment_phase,
                "image_meta": msg.image_meta,
            })
            total_tokens += msg_cost

        return result, len(result) < original_count

    # ── 消息构建 ──────────────────────────────────────────────

    def _format_message_for_session(
        self, msg_dict: Dict[str, Any], is_group: bool,
    ) -> str:
        """按 session 格式格式化单条消息。

        私聊: [HH:MM] [图片 hash] [表情 hash] 消息内容
        群聊: [HH:MM] [speaker_name] [图片 hash] 消息内容
        assistant: 回复内容（无前缀）
        """
        from ..chat.context import _build_image_markers

        role = msg_dict.get("role", "user")
        if role == "assistant":
            return msg_dict.get("content", "")

        content = msg_dict.get("content", "")
        created_at = msg_dict.get("created_at")
        ts = ""
        if created_at:
            if isinstance(created_at, datetime):
                ts = created_at.strftime("%H:%M")
            else:
                ts = str(created_at)

        img_prefix = _build_image_markers(msg_dict)

        if is_group:
            return _format_group_message(msg_dict, ts, img_prefix, content)
        else:
            return f"[{ts}] {img_prefix}{content}"

    @staticmethod
    def _hash_facts(facts: dict) -> str:
        import hashlib
        import json as _json
        raw = _json.dumps(facts, sort_keys=True, ensure_ascii=False)
        return hashlib.md5(raw.encode()).hexdigest()

    def _collect_notifications(
        self,
        decision,
        scope_id: str,
        relation_label: str,
        lore_keys: set,
        now,
        profile,
    ) -> List[str]:
        """组装通知列表（6a-6d），从 _build_messages 提取以缩短方法长度。"""
        notifications = list(decision.notifications)

        # 6a. 关系变化
        prev_label = self.session_manager.get_relation_label(scope_id)
        if prev_label is not None and relation_label != prev_label:
            notifications.append(f"[通知] 你和用户的关系变得更{relation_label}了。")
        self.session_manager.set_relation_label(scope_id, relation_label)

        # 6b. 世界书新命中（纯增量）
        tracker = self.session_manager.get_tracker(scope_id)
        new_lore = lore_keys - tracker.get("activated_lore_keys", set())
        if new_lore:
            for key in new_lore:
                notifications.append(f"[通知] 【世界书】{key}")
            tracker["activated_lore_keys"] |= new_lore

        # 6d. profile facts 变更
        if profile and profile.facts:
            current_hash = self._hash_facts(profile.facts)
            if current_hash != tracker.get("last_profile_hash"):
                facts_lines = "\n".join([f"- {k}: {v}" for k, v in profile.facts.items()])
                notifications.append(f"[通知] 你对用户有了新的了解：\n{facts_lines}")
                tracker["last_profile_hash"] = current_hash

        return notifications

    async def _collect_event_notifications(self, scope_id: str, now: datetime) -> Tuple[List[str], List["DailyEvent"]]:
        """获取今日事件，与 notified_event_ids 差集，仅注入窗口内事件。

        Args:
            scope_id: 用户或群组 ID
            now: 当前壁钟时间，由 _build_messages 传入，保证全链条时间一致
        """
        from ..data.protocols import DailyEvent

        tracker = self.session_manager.get_tracker(scope_id)
        today_str = now.strftime("%Y-%m-%d")

        last_diary_date = tracker.get("last_event_notification_date")
        # 类型归一化：兼容热重载场景下旧 tracker 中残留的 date 对象
        if isinstance(last_diary_date, date):
            last_diary_date = last_diary_date.strftime("%Y-%m-%d")
        if last_diary_date is not None and last_diary_date != today_str:
            tracker["notified_event_ids"] = set()
        tracker["last_event_notification_date"] = today_str

        context_since = tracker.get("last_context_update_at")

        events = await self.store.get_daily_events(today_str)
        if not events:
            tracker["last_context_update_at"] = now
            return [], []

        notified_ids: set = tracker["notified_event_ids"]
        notifications = []
        for e in events:
            if e.id is not None and e.id not in notified_ids:
                # 正向条件：仅注入 created_at > context_since 的事件
                # context_since 为 None（新 session 首次构建或进程重启后）时，
                # 条件恒为 False，所有旧事件仅标记为已见，不注入
                if context_since is not None and e.created_at and e.created_at > context_since:
                    text = e.context_summary if e.context_summary else e.description
                    prefix = format_timestamp(e.created_at, now)
                    rel = format_relative_time(e.created_at, now)
                    time_part = f"{prefix} {rel}" if rel else prefix
                    notifications.append(f"[通知][{time_part}] {text}")
                notified_ids.add(e.id)

        tracker["last_context_update_at"] = now
        return notifications, events

    async def _build_messages(
        self,
        user_id: str,
        group_id: str,
        ctx: ChatCallContext,
    ) -> List[Dict[str, str]]:
        is_group = bool(group_id)
        scope_id = group_id or user_id
        transient_message = ctx.transient_message
        nickname = ctx.nickname
        # now 统一计算并向下透传：_collect_event_notifications 的窗口过滤、
        # 时间前缀、_build_diary_context 的时间计算均使用同一壁钟时间，
        # 禁止各方法内部独立调用 wall_now()
        now = wall_now(self.config.timezone)

        # 防御性回退：session_manager 未注入时不崩溃（factory 中 ChatSession 构造早于 session_manager 注入）
        if self.session_manager is None:
            return await self._build_messages_legacy(user_id, group_id, ctx=ctx)

        # 1. 构建 static_prompt + hash
        static_prompt = self.context_builder.build_static_prompt()
        from ..chat.session_manager import SessionManager
        static_hash = SessionManager.compute_static_hash(static_prompt)

        decision = await self.session_manager.get_or_create(
            user_id=scope_id,
            character_id=self.character.character_id,
            static_prompt=static_prompt,
            static_hash=static_hash,
            is_group=is_group,
        )
        session = decision.session

        # 3. 加载已有消息
        session_msgs = await self.store.get_session_messages(session.session_id)
        # 转换为 dict 格式以便处理
        session_msg_dicts = [
            {"role": m.role, "content": m.content, "created_at": m.created_at}
            for m in session_msgs
        ]

        # 4. 跨天检测 + 格式化当前消息
        last_msg = session_msgs[-1] if session_msgs else None
        if last_msg and last_msg.created_at:
            last_date = last_msg.created_at.date() if hasattr(last_msg.created_at, 'date') else None
            # 跨天检测（统一用 UTC 日期比较，避免本地时间与 UTC 偏移导致的误触发或遗漏）
            today_utc = datetime.now(tz.utc).date()
            if last_date and last_date != today_utc:
                cross_day = _make_time_notification(self.config.timezone)
                cross_day_msg = PersonaSessionMessage(
                    session_id=session.session_id,
                    role="user",
                    content=cross_day,
                )
                await self.session_manager.append_messages(session.session_id, [cross_day_msg])
                session_msg_dicts.append({"role": "user", "content": cross_day})

        # 格式化并追加当前用户消息
        # history_dicts 仍需获取（供 _build_lore_sections 扫描），但不在 transient 路径中取 [-1]
        history_dicts, _ = await self._fetch_short_term_history(user_id, group_id)

        if transient_message is not None:
            # 临时消息路径：仅注入当前 LLM 上下文，不持久化到 session
            # 适用于 jrrp 等不需要在对话历史中累积的事件消息。
            # 参考 SillyTavern 的 injected 标记方案：若未来多源注入（如生命事件、
            # 系统通知）可考虑在消息上增加标记字段区分来源，而非仅靠旁路参数。
            #
            # 当前为单源过渡版（transient_message: Optional[str]），
            # 理想设计：transient_messages: List[InjectedMessage] where
            #   InjectedMessage = {"source": str, "content": str, "priority": int}
            #   按 priority 降序注入，同级按 list 顺序。
            #   _format_message_for_session 根据 source 选择不同格式化模板。
            #   迁移时下方 "系统" display_name 改为 source 映射。
            ts = now.strftime("%H:%M")
            transient_dict: Dict[str, Any] = {
                "role": "user",
                "content": transient_message,
                "created_at": now,
                "display_name": "系统",
            }
            formatted_current = self._format_message_for_session(transient_dict, is_group)
            session_msg_dicts.append({"role": "user", "content": formatted_current})
        elif history_dicts:
            current_msg_dict = history_dicts[-1]
            formatted_current = self._format_message_for_session(current_msg_dict, is_group)
            current_persona_msg = PersonaSessionMessage(
                session_id=session.session_id,
                role=current_msg_dict.get("role", "user"),
                content=formatted_current,
            )
            await self.session_manager.append_messages(session.session_id, [current_persona_msg])
            session_msg_dicts.append({"role": current_persona_msg.role, "content": formatted_current})

        # 5. 获取动态数据
        profile = await self.store.get_user_profile(user_id)
        rel = await self.store.get_relationship(user_id)
        relation_label = self._resolve_relation_label(user_id, rel)

        # 世界书扫描
        lore_sections = self._build_lore_sections(history_dicts)
        lore_keys = set()
        for section_list in lore_sections.values():
            for entry_text in section_list:
                lore_keys.add(entry_text)

        # 6. 组装通知列表（含增量事件通知）
        notifications = self._collect_notifications(
            decision, scope_id, relation_label, lore_keys, now, profile,
        )
        event_notes, daily_events = await self._collect_event_notifications(scope_id, now)
        notifications.extend(event_notes)
        diary_context = await self._build_diary_context(events=daily_events, now=now)

        # 7. 构建最终消息列表
        result = self.context_builder.build(
            messages=session_msg_dicts,
            static_prompt=static_prompt,
            notifications=notifications,
            relation_label=relation_label,
        )

        # 调试信息
        debug_info = self.context_builder.build_debug_info(
            short_term_history=session_msg_dicts,
            user_profile=profile,
            diary_context=diary_context,
            relation_label=relation_label,
            lore_sections=lore_sections,
        )
        logger.debug(f"context_debug: {debug_info}")

        return result

    async def _build_messages_legacy(
        self,
        user_id: str,
        group_id: str,
        ctx: ChatCallContext,
    ) -> List[Dict[str, str]]:
        """旧版 _build_messages（当 session_manager 为 None 时回退）"""
        history_dicts, _ = await self._fetch_short_term_history(user_id, group_id)
        is_group = bool(group_id)

        formatted = self.context_builder.format_history(history_dicts, is_group)
        truncated = self.context_builder.truncate_by_turns(
            formatted,
            self.config.max_history_turns,
            self.config.max_history_tokens,
        )

        # transient_message 注入（与主路径保持一致的格式化逻辑）
        if ctx.transient_message is not None:
            ts = wall_now(self.config.timezone).strftime("%H:%M")
            transient_dict: Dict[str, Any] = {
                "role": "user",
                "content": ctx.transient_message,
                "created_at": wall_now(self.config.timezone),
                "display_name": "系统",
            }
            formatted_transient = self._format_message_for_session(transient_dict, is_group)
            truncated.append({"role": "user", "content": formatted_transient})

        profile = await self.store.get_user_profile(user_id)
        rel = await self.store.get_relationship(user_id)
        relation_label = self._resolve_relation_label(user_id, rel)
        diary_context = await self._build_diary_context()
        lore_sections = self._build_lore_sections(history_dicts)

        debug_info = self.context_builder.build_debug_info(
            short_term_history=truncated,
            user_profile=profile,
            diary_context=diary_context,
            relation_label=relation_label,
            lore_sections=lore_sections,
        )
        logger.debug(f"context_debug (legacy): {debug_info}")

        return self.context_builder.build(
            formatted_history=truncated,
            history_dicts=history_dicts,
            user_profile=profile,
            diary_context=diary_context,
            relation_label=relation_label,
        )

    def _resolve_relation_label(self, user_id: str, rel: Optional[RelationshipState]) -> str:
        """根据关系状态（含衰减计算）解析关系标签"""
        if rel:
            if self._scoring_trigger:
                rel = self._scoring_trigger.effective_relationship(rel)
            _, relation_label = rel.get_relation_level(self.character.get_relation_labels())
        else:
            temp_rel = RelationshipState(user_id=user_id, familiarity=0.0, intimacy=0.0)
            _, relation_label = temp_rel.get_relation_level(self.character.get_relation_labels())

        return relation_label

    async def _build_diary_context(self, events: Optional[List["DailyEvent"]] = None, now: Optional[datetime] = None) -> str:
        """构建日记/事件上下文：优先今日事件，fallback 昨日日记

        Args:
            events: 可选的今日事件列表。若传入则复用该列表，跳过内部 get_daily_events 查询。
            now: 可选的当前壁钟时间。传入则复用（与 _build_messages 时间一致），None 时内部 wall_now。
        """
        from ..data.protocols import DailyEvent

        wall = now if now is not None else wall_now(self.config.timezone)
        today = wall.strftime("%Y-%m-%d")
        yesterday = (wall - timedelta(days=1)).strftime("%Y-%m-%d")
        max_diary_len = self.config.max_diary_context_chars

        if events is None:
            events = await self.store.get_daily_events(today)
        if events:
            valid_events = [
                e for e in events
                if (e.context_summary and e.context_summary.strip())
                or (e.description and e.description.strip())
            ]
            # 按时间升序排列（旧→新），使日记以自然时序呈现
            valid_events.sort(key=lambda e: e.created_at or DEFAULT_EPOCH, reverse=False)

            if valid_events:
                # 优先用 context_summary，空则回退到 description
                summaries = []
                for e in valid_events:
                    prefix = format_timestamp(e.created_at, wall)
                    rel = format_relative_time(e.created_at, wall)
                    full_prefix = f"{prefix} {rel}" if rel else prefix
                    text = e.context_summary if e.context_summary else e.description
                    summaries.append(f"[{full_prefix}] {text}" if prefix else text)
                diary_context = "今天发生的事：" + "；".join(summaries)
                if len(diary_context) > max_diary_len:
                    diary_context = diary_context[:max_diary_len].rsplit('；', 1)[0] + "..."
                return diary_context

        diary = await self.store.get_diary(yesterday)
        if diary:
            if len(diary) > max_diary_len:
                diary = diary[:max_diary_len] + "..."
            return "昨天的日记：" + diary

        return ""

    def _build_lore_sections(
        self,
        history_dicts: List[Dict[str, str]],
    ) -> Dict[str, List[str]]:
        """构建世界书 lore 段落"""
        return self.context_builder.build_lore_text(history_dicts)


