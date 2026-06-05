"""对话会话管理器

负责构造 LLM 上下文、调用 router、处理工具回调、执行评分。
通过依赖注入接收 store/router/tool_registry 等，零外部 import。

计费统一走 ``BillingPolicy.charge()``，由 ``_coordinator_on_result``
（中间轮）与 ``_chat_via_coordinator``（最终轮）两处调用，
避免注释不变量依赖人工维护。
"""
from typing import List, Dict, Optional, Any, Tuple, TYPE_CHECKING
import asyncio
import json
import time
import random
from datetime import datetime, timedelta, timezone as tz

from utils.string import estimate_tokens
from utils.logger import logger

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
from ..tools.registry import ToolRegistry, ToolDomain
from ..tools.context import ToolContext
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


class ChatSession:
    """对话会话管理器 — 负责对话编排、门控、上下文构建、工具调用委托"""

    def __init__(
        self,
        store: PersonaDataStore,
        router: LLMRouter,
        tool_registry: ToolRegistry,
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
        self.tool_registry = tool_registry
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
        # 动态信息追踪统一由 session_manager 管理（get_warmth_label / set_warmth_label）

    def update_character(self, character: Character) -> None:
        """同步新的角色卡引用"""
        self.character = character
        self.context_builder.update_character(character)
        self._scoring_trigger.update_character(character)

    # ── 公开 API ──────────────────────────────────────────────

    async def chat(
        self,
        user_id: str,
        group_id: str,
        message: str,
        nickname: str = "",
        image_data_urls: Optional[List[str]] = None,
    ) -> Optional[str]:
        """处理单条用户消息，返回回复文本（None 表示不回复）"""
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

        response: Optional[str] = None
        try:
            is_chat_message = not message.startswith(".") or message.lower().startswith(".ai")

            # 睡眠门控（仅拦截聊天消息，命令透传）
            if is_chat_message and self._sleep_gate is not None and not await self._sleep_gate.is_awake():
                msgs = self.character.extensions.sleep_messages
                if msgs is None:
                    msgs = _DEFAULT_SLEEP_MESSAGES
                if msgs:
                    msg = random.choice(msgs)
                    logger.info(f"睡眠门控触发: user={user_id}, character={self.character.name}")
                    response = msg
                    return response

            if self.config.relationship_refuse_enabled and is_chat_message:
                if group_id:
                    history = await self.store.get_group_messages(group_id, limit=1)
                else:
                    history = await self.store.get_recent_messages(user_id, group_id="", limit=1)
                is_first = len(history) == 0
                if not is_first:
                    rel = await self.store.get_relationship(user_id)
                    if rel:
                        if self._scoring_trigger:
                            rel = self._scoring_trigger.effective_relationship(rel)
                        warmth_level, _ = rel.get_warmth_level(self.character.get_warmth_labels())
                        if warmth_level == 0:
                            score = rel.composite_score
                            base = self.config.relationship_refuse_prob_base
                            max_p = self.config.relationship_refuse_prob_max
                            # 仅在 warmth_level==0（冷淡）时触发拒绝；阶段边界处的概率跳变是预期行为
                            p_refuse = base + (max_p - base) * (1 - score / 20)
                            if random.random() < p_refuse:
                                default_refuse_messages = [
                                    "...（对方似乎没有兴趣理你）",
                                    "...（已读不回）",
                                    "嗯。",
                                ]
                                char_refuse = self.character.extensions.refuse_messages
                                refuse_messages = char_refuse if char_refuse is not None else default_refuse_messages

                                if refuse_messages:
                                    refuse_response = random.choice(refuse_messages)
                                    logger.info(
                                        f"冷淡拒绝触发: user={user_id}, score={score:.2f}, "
                                        f"p_refuse={p_refuse:.2%}"
                                    )
                                    response = refuse_response
                                    return response

            target_key = f"group:{group_id}" if group_id else f"user:{user_id}"

            logger.debug(
                f"[Persona] ChatSession.chat enter: user={user_id} group={group_id}"
                f" message_len={len(message) if message else 0}"
                f" image_count={len(image_data_urls) if image_data_urls else 0}"
                f" target_key={target_key}"
            )
            response = await self._chat_via_coordinator(user_id, group_id, message, target_key, image_data_urls=image_data_urls)
            return response

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("对话处理失败")
            response = "抱歉，我出错了，请稍后再试..."
            return response
        finally:
            logger.debug(
                f"[Persona] ChatSession.chat return: user={user_id}"
                f" return_type={type(response).__name__}"
                f" return_len={len(response) if response else 0}"
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

    # ── coordinator 回调 ──────────────────────────────────────

    async def _coordinator_chat_call_fn(
        self, user_id: str, group_id: str, messages: List[str],
        image_data_urls: Optional[List[str]] = None,
    ) -> Optional[str]:
        """coordinator chat 路径的单轮 LLM 调用。"""
        current_message = "\n".join(messages) if messages else ""

        messages_for_llm = await self._build_messages(user_id, group_id)

        response, delivery_performed = await self._chat_with_tools(
            user_id, group_id, messages_for_llm, image_data_urls=image_data_urls,
        )

        if delivery_performed:
            logger.debug(
                f"[Persona] _coordinator_chat_call_fn return: user={user_id}"
                f" delivery_performed=True will_return_empty=True"
                f" dropped_response_len={len(response) if response else 0}"
            )
            return ""

        await self._after_response(user_id, group_id, current_message, response)

        # Session 后处理：追加 assistant 消息 + token 估算 + 压缩检查
        if self.session_manager:
            await self.session_manager.on_chat_complete(user_id, group_id, response, current_message, self.router)

        logger.debug(
            f"[Persona] _coordinator_chat_call_fn return: user={user_id}"
            f" delivery_performed=False will_return_empty=False"
            f" response_len={len(response) if response else 0}"
        )
        return response

    async def _coordinator_on_exhausted(
        self,
        user_id: str,
        group_id: str,
        current_message: str,
        last_exception: Optional[Exception] = None,
    ) -> str:
        """coordinator 耗尽时的兜底回复。"""
        if last_exception is not None:
            logger.error(f"[Persona] coordinator on_exhausted: exception={type(last_exception).__name__}: {last_exception}")
        if isinstance(last_exception, QuotaExceeded):
            fallback_response = (
                f"{last_exception}\n\n"
                "使用 `.ai key config` 配置自己的 API Key 可解除限制"
            )
        else:
            fallback_response = "LLM服务暂时不可用，请稍后再试"
        await self._response_handler.persist_and_send(user_id, group_id, fallback_response)
        await self._after_response(user_id, group_id, current_message, fallback_response)
        if self._response_handler.port is not None:
            logger.info(
                f"[Persona] on_exhausted: fallback sent via port user={user_id}"
            )
            return ""
        else:
            logger.warning(
                f"_coordinator_on_exhausted: MessagePort 未注入，"
                f"消息无法发送 (user={user_id}, group={group_id})"
            )
            return fallback_response

    async def _coordinator_on_result(self, user_id: str, group_id: str, result: str) -> None:
        """中间轮结果：通过 MessagePort 发送、持久化、扣减配额。

        计费原则：按 coordinator 轮次算，每轮 1 次配额扣减。

        - coordinator 外层循环每跑 1 轮 = 1 次扣费
          （无论非分段/分段输出，也不论该轮内部因工具循环/分段/round callback
           调了多少次 LLM API；内层 LLM API 调用次数与配额扣减无关）
        - max_iterations 是外层循环上限（防刷屏），正常使用不会触及
        - 分段路径下，本函数提前返回（消息已通过 send_reply_segment + dispatcher
          实时发送，无需在此重发也无需写历史），但 charge 应在早返之前完成

        场景对照（假设单次 LLM 调用从开始到完成耗时 5 秒）：

        A. 单条消息 + 非分段输出
           t=0 发 msg_1；之后无新消息
           → coordinator 跑 1 轮 → 1 次扣费（仅最终轮 success）

        B. 单条消息 + 分段输出
           t=0 发 msg_1；之后无新消息
           → coordinator 跑 1 轮 → 1 次扣费（仅最终轮 success）

        C. 在第 1 轮期间发 1 条新消息 + 非分段输出
           t=0 发 msg_1（开始 iter 1）；t=2 发 msg_2（iter 1 期间，进 buffer）；
           t=5 iter 1 完成、发送 msg_1 的回复、开始 iter 2 处理 msg_2；
           t=10 iter 2 完成、发送 msg_2 的回复，无新消息 → 退出
           → coordinator 跑 2 轮 → 2 次扣费（iter 1 触发本函数 1 次 + 最终 success 1 次）

        D. 在第 1 轮期间发 1 条新消息 + 分段输出
           同 C 时间轴
           → coordinator 跑 2 轮 → 2 次扣费

        E. 连续在每轮期间发新消息 + 非分段输出
           t=0 发 msg_1（开始 iter 1）；t=2 发 msg_2（iter 1 期间，进 buffer）；
           t=5 iter 1 完成、发送 msg_1 的回复、开始 iter 2 处理 msg_2；
           t=7 发 msg_3（iter 2 期间，进 buffer）；
           t=10 iter 2 完成、发送 msg_2 的回复、开始 iter 3 处理 msg_3；
           t=15 iter 3 完成、发送 msg_3 的回复，无新消息 → 退出
           → coordinator 跑 3 轮 → 3 次扣费

        F. 连续在每轮期间发新消息 + 分段输出
           同 E 时间轴
           → coordinator 跑 3 轮 → 3 次扣费
        """
        if not result:
            return

        await self._response_handler.persist_and_send(user_id, group_id, result)

    async def _chat_via_coordinator(
        self, user_id: str, group_id: str, message: str, target_key: str,
        image_data_urls: Optional[List[str]] = None,
    ) -> Optional[str]:
        fallback_response: Optional[str] = None
        current_message_for_exhausted = message

        async def chat_call_fn(messages: List[str]) -> Optional[str]:
            nonlocal current_message_for_exhausted
            current_message = "\n".join(messages) if messages else message
            current_message_for_exhausted = current_message
            return await self._coordinator_chat_call_fn(
                user_id, group_id, messages, image_data_urls=image_data_urls,
            )

        async def on_exhausted(last_exception: Optional[Exception] = None):
            nonlocal fallback_response
            fallback_response = await self._coordinator_on_exhausted(
                user_id, group_id, current_message_for_exhausted, last_exception
            )

        async def _on_result(result: str):
            await self._coordinator_on_result(user_id, group_id, result)

        result = await self.coordinator.submit(
            target_key,
            message,
            chat_call_fn,
            continue_on_buffered=True,
            on_exhausted=on_exhausted,
            on_result=_on_result,
        )
        if result.status == "success":
            # 计费由 UsageSink 在 AgentRuntime 内 best effort 处理。
            logger.info(
                f"[Persona] _chat_via_coordinator return: user={user_id}"
                f" result.status=success fallback_used={fallback_response is not None}"
                f" value_len={len(result.value) if result.value else 0}"
            )
            return result.value
        logger.info(
            f"[Persona] _chat_via_coordinator return: user={user_id}"
            f" result.status={result.status} fallback_used={fallback_response is not None}"
        )
        return fallback_response if fallback_response is not None else None

    # ── 工具调用 ──────────────────────────────────────────────

    async def _chat_with_tools(
        self, user_id: str, group_id: str, messages: List[Dict],
        image_data_urls: Optional[List[str]] = None,
    ) -> Tuple[str, bool]:
        from ..agent.runtime import AgentRuntime
        from ..agent.tool_bridge import build_registry
        from ..agent.request import AgentRunLimits

        limits = AgentRunLimits(
            max_tool_rounds=self.config.tools_max_rounds,
        )

        runtime = AgentRuntime(
            router=self.router,
            store=self.store,
            port=self._response_handler.port,
            limits=limits,
        )

        ctx = ToolContext(
            user_id=user_id, group_id=group_id, store=self.store,
            send=self._response_handler.port,
            query=self.query_store, resolve_db=self.resolve_db,
            timezone=self.config.timezone,
        )

        new_registry = build_registry(
            self.tool_registry, [ToolDomain.CHAT], ctx=ctx,
        )

        result = await runtime.run_chat(
            messages=messages, user_id=user_id, group_id=group_id,
            tool_registry=new_registry,
            image_data_urls=image_data_urls,
        )

        if result.status != "completed":
            logger.error(f"[Persona] AgentRun 失败: status={result.status}, reason={result.final_reason}")
            return ("抱歉，我出错了，请稍后再试...", False)

        delivery_performed = result.delivery_performed and result.final_reason != "direct_content"
        if delivery_performed:
            logger.info(
                f"[Persona] delivery_performed=True user={user_id}"
                f" final_reason={result.final_reason}"
            )

        return (result.final_text, delivery_performed)

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
                    "turn_id": msg.turn_id,
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
                "turn_id": msg.turn_id,
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
        warmth_label: str,
        lore_keys: set,
        now,
        profile,
    ) -> List[str]:
        """组装通知列表（6a-6d），从 _build_messages 提取以缩短方法长度。"""
        notifications = list(decision.notifications)

        # 6a. 温暖度变化
        prev_label = self.session_manager.get_warmth_label(scope_id)
        if prev_label is not None and warmth_label != prev_label:
            notifications.append(f"[通知] 你和用户的关系变得更{warmth_label}了。")
        self.session_manager.set_warmth_label(scope_id, warmth_label)

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

    async def _collect_event_notifications(self, scope_id: str, today: "date") -> Tuple[List[str], List["DailyEvent"]]:
        """获取今日事件，与 notified_event_ids 差集，每个新事件生成独立通知。

        跨天时自动 reset notified_event_ids 集合。
        返回 (通知列表, 事件列表)，调用方可复用事件列表避免重复 SQL 查询。
        """
        from ..data.protocols import DailyEvent

        tracker = self.session_manager.get_tracker(scope_id)
        today_str = today.strftime("%Y-%m-%d")

        last_diary_date = tracker.get("last_event_notification_date")
        if last_diary_date is not None and last_diary_date != today:
            tracker["notified_event_ids"] = set()
        tracker["last_event_notification_date"] = today

        events = await self.store.get_daily_events(today_str)
        if not events:
            return [], []

        notified_ids: set = tracker["notified_event_ids"]
        notifications = []
        for e in events:
            if e.id is not None and e.id not in notified_ids:
                text = e.context_summary if e.context_summary else e.description
                notifications.append(f"[通知] {text}")
                notified_ids.add(e.id)

        return notifications, events

    async def _build_messages(
        self,
        user_id: str,
        group_id: str,
    ) -> List[Dict[str, str]]:
        is_group = bool(group_id)
        scope_id = group_id or user_id
        now = wall_now(self.config.timezone)

        # 防御性回退：session_manager 未注入时不崩溃（factory 中 ChatSession 构造早于 session_manager 注入）
        if self.session_manager is None:
            return await self._build_messages_legacy(user_id, group_id)

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

        # 格式化并追加当前用户消息（从 _fetch_short_term_history 获取）
        history_dicts, _ = await self._fetch_short_term_history(user_id, group_id)
        if history_dicts:
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
        warmth_label = self._resolve_warmth_label(user_id, rel)

        # 世界书扫描
        lore_sections = self._build_lore_sections(history_dicts)
        lore_keys = set()
        for section_list in lore_sections.values():
            for entry_text in section_list:
                lore_keys.add(entry_text)

        # 6. 组装通知列表（含增量事件通知）
        notifications = self._collect_notifications(
            decision, scope_id, warmth_label, lore_keys, now, profile,
        )
        event_notes, daily_events = await self._collect_event_notifications(scope_id, now.date())
        notifications.extend(event_notes)
        diary_context = await self._build_diary_context(events=daily_events)

        # 7. 构建最终消息列表
        result = self.context_builder.build(
            messages=session_msg_dicts,
            static_prompt=static_prompt,
            notifications=notifications,
            warmth_label=warmth_label,
        )

        # 调试信息
        debug_info = self.context_builder.build_debug_info(
            short_term_history=session_msg_dicts,
            user_profile=profile,
            diary_context=diary_context,
            warmth_label=warmth_label,
            lore_sections=lore_sections,
        )
        logger.debug(f"context_debug: {debug_info}")

        return result

    async def _build_messages_legacy(
        self,
        user_id: str,
        group_id: str,
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

        profile = await self.store.get_user_profile(user_id)
        rel = await self.store.get_relationship(user_id)
        warmth_label = self._resolve_warmth_label(user_id, rel)
        diary_context = await self._build_diary_context()
        lore_sections = self._build_lore_sections(history_dicts)

        debug_info = self.context_builder.build_debug_info(
            short_term_history=truncated,
            user_profile=profile,
            diary_context=diary_context,
            warmth_label=warmth_label,
            lore_sections=lore_sections,
        )
        logger.debug(f"context_debug (legacy): {debug_info}")

        return self.context_builder.build(
            formatted_history=truncated,
            history_dicts=history_dicts,
            user_profile=profile,
            diary_context=diary_context,
            warmth_label=warmth_label,
        )

    def _resolve_warmth_label(self, user_id: str, rel: Optional[RelationshipState]) -> str:
        """根据关系状态（含衰减计算）解析温暖度标签"""
        initial = float(self.character.extensions.initial_relationship)

        if rel:
            if self._scoring_trigger:
                rel = self._scoring_trigger.effective_relationship(rel)
            _, warmth_label = rel.get_warmth_level(self.character.get_warmth_labels())
        else:
            temp_rel = RelationshipState(
                user_id=user_id, intimacy=initial, passion=initial,
                trust=initial, secureness=initial,
            )
            _, warmth_label = temp_rel.get_warmth_level(self.character.get_warmth_labels())

        return warmth_label

    async def _build_diary_context(self, events: Optional[List["DailyEvent"]] = None) -> str:
        """构建日记/事件上下文：优先今日事件，fallback 昨日日记

        Args:
            events: 可选的今日事件列表。若传入则复用该列表，跳过内部 get_daily_events 查询。
        """
        from ..data.protocols import DailyEvent

        wall = wall_now(self.config.timezone)
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


