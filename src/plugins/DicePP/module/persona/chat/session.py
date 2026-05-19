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
from datetime import datetime, timedelta

from nonebot.log import logger

from utils.string import estimate_tokens

from ..data.store import PersonaDataStore
from ..data.models import (
    RelationshipState,
    UnifiedMessage,
    MessageType,
)
from ..llm.router import LLMRouter, QuotaExceeded
from ..llm.selection import SelectionPolicy
from ..character.models import Character
from ..chat.context import ContextBuilder
from ..chat.chat_config import ChatConfig
from ..game.decay import DecayCalculator
from ..wall_clock import persona_wall_now, PERSONA_EPOCH, format_timestamp, format_relative_time
from ..tools.registry import ToolRegistry, ToolDomain
from ..tools.context import ToolContext
from ..life.protocols import SleepGate
from ..llm.coordinator import LLMCallCoordinator
from ..gateway.port import MessagePort
from .segment_dispatcher import SegmentDispatcher, SegmentItem
from .segment_state import SegmentBudgetState, SegmentLimits

_DEFAULT_SLEEP_MESSAGES = ("角色正在休息，请稍后再来",)

if TYPE_CHECKING:
    from .scoring_trigger import ScoringTrigger
    from .response_handler import ResponseHandler


class ChatSession:
    """对话会话管理器 — 负责对话编排、门控、上下文构建、工具调用委托"""

    class _SegmentedSentinel(str):
        """标记分段路径的哨兵值，继承 str 以保持与 coordinator 的兼容性。"""

        pass

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
        segment_dispatcher: Optional[SegmentDispatcher] = None,
        query_store: Any = None,
        resolve_db: Any = None,
        sleep_gate: Optional[SleepGate] = None,
    ):
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
        self.segment_dispatcher = segment_dispatcher
        self.query_store = query_store
        self.resolve_db = resolve_db
        self._sleep_gate = sleep_gate
        self._last_messages: Dict[str, Tuple[str, float]] = {}

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
                    return msg

            if self.config.relationship_refuse_enabled and is_chat_message:
                if group_id:
                    history = await self.store.get_group_unified_messages(group_id, limit=1)
                else:
                    history = await self.store.get_recent_unified_messages(user_id, group_id="", limit=1)
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
                                    return refuse_response

            target_key = f"group:{group_id}" if group_id else f"user:{user_id}"

            response = await self._chat_via_coordinator(user_id, group_id, message, target_key)
            return response

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("对话处理失败")
            return "抱歉，我出错了，请稍后再试..."

    async def clear_history(self, user_id: str, group_id: str) -> None:
        """清空对话历史"""
        await self.store.clear_messages(user_id, group_id)

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
        self, user_id: str, group_id: str, messages: List[str]
    ) -> Optional[str]:
        """coordinator chat 路径的单轮 LLM 调用。"""
        current_message = "\n".join(messages) if messages else ""

        messages_for_llm = await self._build_messages(user_id, group_id)

        response = await self._chat_with_tools(user_id, group_id, messages_for_llm)

        if isinstance(response, self._SegmentedSentinel):
            # 分段路径：历史已由 _chat_with_tools 写入，跳过重复持久化
            await self._after_response(user_id, group_id, current_message, str(response))
            return self._SegmentedSentinel("")

        # 非分段路径：持久化由 _coordinator_on_result 统一完成
        await self._after_response(user_id, group_id, current_message, response)
        return response

    async def _coordinator_on_exhausted(
        self,
        user_id: str,
        group_id: str,
        current_message: str,
        last_exception: Optional[Exception] = None,
    ) -> str:
        """coordinator 耗尽时的兜底回复。"""
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
            return self._SegmentedSentinel("")
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
        if isinstance(result, self._SegmentedSentinel):
            return

        await self._response_handler.persist_and_send(user_id, group_id, result)

    async def _chat_via_coordinator(
        self, user_id: str, group_id: str, message: str, target_key: str
    ) -> Optional[str]:
        fallback_response: Optional[str] = None
        current_message_for_exhausted = message

        async def chat_call_fn(messages: List[str]) -> Optional[str]:
            nonlocal current_message_for_exhausted
            current_message = "\n".join(messages) if messages else message
            current_message_for_exhausted = current_message
            return await self._coordinator_chat_call_fn(user_id, group_id, messages)

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
            # 最终轮计费由 BillingHook 处理（每次 AgentLoop.run() 首次 post_llm 扣费）。
            # _SegmentedSentinel 是 str 子类，直接透传——
            # 调用方用 `is None` 区分"未进入 chat"，用 `bool()` 区分"是否需要再发"
            return result.value
        return fallback_response if fallback_response is not None else None

    # ── 工具调用 ──────────────────────────────────────────────

    async def _chat_with_tools(
        self, user_id: str, group_id: str, messages: List[Dict],
    ) -> str:
        target_key = SegmentDispatcher.target_key(user_id, group_id)

        if self.segment_dispatcher:
            await self.segment_dispatcher.flush(target_key)

        tools = self.tool_registry.get_definitions_for(ToolDomain.CHAT)

        segment_state = None
        if self.segment_dispatcher:
            segment_limits = SegmentLimits(
                max_chars=self.config.segment_max_chars,
                soft_limit=self.config.segment_soft_limit,
                hard_limit=self.config.segment_hard_limit,
                count_max=self.config.segment_count_max,
                max_delay=self.config.segment_max_delay,
            )
            segment_state = SegmentBudgetState(limits=segment_limits)

        ctx = ToolContext(
            user_id=user_id, group_id=group_id, store=self.store,
            send=self._response_handler.port, segment_dispatcher=self.segment_dispatcher,
            segment_state=segment_state,
            query=self.query_store, resolve_db=self.resolve_db,
        )

        # 组装 Hooks（通过 Router 工厂方法避免重复构造逻辑）
        hooks = self.router.make_default_hooks(
            include_billing=True, include_segment=bool(segment_state))

        result = await self.router.run_via_loop(
            messages=messages, tools=tools,
            max_tool_rounds=self.config.tools_max_rounds,
            selection=SelectionPolicy.CHAT,
            user_id=user_id, group_id=group_id,
            tool_registry=self.tool_registry,
            tool_domains=[ToolDomain.CHAT], tool_ctx=ctx,
            hooks=hooks,
        )

        if result.aborted:
            raise QuotaExceeded(result.abort_reason)

        metadata = result.metadata
        content = result.final_output or ""

        if metadata.get("tool_rounds", 0) > 0:
            logger.debug(
                f"工具调用完成: user={user_id}, "
                f"rounds={metadata.get('tool_rounds')}, "
                f"tools={metadata.get('tool_names')}, "
                f"cached={metadata.get('cached_tokens', 0)}")

        if self.segment_dispatcher:
            return await self._run_chat_with_tools_segmented(
                user_id, group_id, target_key, content, metadata, segment_state)

        return content

    async def _run_chat_with_tools_segmented(
        self,
        user_id: str,
        group_id: str,
        target_key: str,
        content: str,
        metadata: Dict[str, Any],
        segment_state: SegmentBudgetState,
    ) -> str:
        """分段路径：拼接回复、兜底处理、历史写入"""
        full_reply = "".join(segment_state.buffer)

        # 5.4.4 / 5.4.5: 兜底 — callback 用尽且 LLM 仍未分段
        if metadata.get("callback_count", 0) >= self.config.segment_round_callbacks_max:
            if content:
                # 若 buffer 已有成功发送的分段，说明 LLM 已发完内容、
                # 最后一轮多输出了一句状态文本，保留 buffer 忽略 content。
                # 此处兜底与 ContextBuilder 分段引导 prompt 共同防御 LLM 多余输出，
                # 修改任一需确认另一是否需要同步。
                if segment_state.buffer:
                    logger.warning(
                        f"LLM 已发送 {segment_state.segment_count} 段后输出额外文本，已忽略: "
                        f"user={user_id}, extra={content[:80]!r}"
                    )
                else:
                    fallback_content = content[:self.config.segment_hard_limit]
                    logger.warning(
                        f"LLM 忽略分段工具，使用兜底: user={user_id}, "
                        f"fallback_len={len(fallback_content)}"
                    )
                    if self.segment_dispatcher:
                        await self.segment_dispatcher.flush(target_key)
                    segment_state.buffer.append(fallback_content)
                    segment_state.total_chars += len(fallback_content)
                    segment_state.segment_count += 1
                    self.segment_dispatcher.notify(
                        target_key,
                        SegmentItem(
                            content=fallback_content,
                            delay_before=0.0,
                            user_id=user_id,
                            group_id=group_id,
                        ),
                    )
                    full_reply = "".join(segment_state.buffer)
            else:
                logger.error(f"LLM 耗尽 callback 且返回空 content: user={user_id}")

        # 5.4.6: 写入历史，分段已由 dispatcher 实时发出，直接标记 sent_ok=1
        effective_user_id = "assistant" if group_id else user_id
        msg_id = await self.store.add_unified_message(
            user_id=effective_user_id,
            group_id=group_id or "",
            role="assistant",
            type=MessageType.CHAT,
            content=full_reply,
            display_name="我",
        )
        await self.store.update_sent_ok(msg_id, 1)

        if self.segment_dispatcher:
            await self.segment_dispatcher.drain(target_key)

        return self._SegmentedSentinel(full_reply)

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
            history = await self.store.get_recent_unified_messages(user_id, group_id="", limit=limit)
            history_dicts = [
                {"role": msg.role, "content": msg.content, "speaker_name": "你" if msg.role == "user" else "我", "created_at": msg.created_at}
                for msg in history
            ]
            return history_dicts, False

        history = await self.store.get_group_unified_messages(group_id, limit=None)
        return self._apply_token_window(history)

    def _apply_token_window(
        self,
        history: List[UnifiedMessage],
    ) -> Tuple[List[Dict[str, str]], bool]:
        """群聊 Token-based 动态窗口（从新到旧收集，升序输出）"""
        if not history:
            return [], False

        original_count = len(history)

        now = persona_wall_now(self.config.timezone)
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
            })
            total_tokens += msg_cost

        return result, len(result) < original_count

    # ── 消息构建 ──────────────────────────────────────────────

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
                trust=initial, secureness=initial
            )
            _, warmth_label = temp_rel.get_warmth_level(self.character.get_warmth_labels())

        return warmth_label

    async def _build_diary_context(self) -> str:
        """构建日记/事件上下文：优先今日事件，fallback 昨日日记"""
        wall = persona_wall_now(self.config.timezone)
        today = wall.strftime("%Y-%m-%d")
        yesterday = (wall - timedelta(days=1)).strftime("%Y-%m-%d")
        max_diary_len = self.config.max_diary_context_chars

        events = await self.store.get_daily_events(today)
        if events:
            valid_events = [
                e for e in events
                if (e.context_summary and e.context_summary.strip())
                or (e.description and e.description.strip())
            ]
            # 按时间升序排列（旧→新），使日记以自然时序呈现
            valid_events.sort(key=lambda e: e.created_at or PERSONA_EPOCH, reverse=False)

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

    async def _build_messages(
        self,
        user_id: str,
        group_id: str,
    ) -> List[Dict[str, str]]:
        # 前置条件：当前用户消息已由 inbound_message_hook 写入 unified_messages，
        # _fetch_short_term_history 返回的历史末尾即为当前消息。
        history_dicts, _ = await self._fetch_short_term_history(user_id, group_id)
        is_group = bool(group_id)

        formatted = self.context_builder.format_history(history_dicts, is_group)
        # 群聊双层截断：_apply_token_window 在存储层按时间窗口 + token budget + max messages
        # 做首轮过滤，truncate_by_turns 在格式化后按 max_history_turns + max_history_tokens 兜底。
        # 当前 max_history_tokens=4000 > group_context_budget_tokens=2000，第二层通常不触发。
        # WHY 群聊保留双层：_apply_token_window 负责群聊特有的时间窗口收敛和说话人合并，
        # 这些逻辑超出 truncate_by_turns 通用截断的职责范围。私聊消息直接来自 DB 按时间升序，
        # 无需首层过滤，因此仅走 truncate_by_turns 单层截断。
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
        logger.debug(f"context_debug: {debug_info}")

        return self.context_builder.build(
            formatted_history=truncated,
            history_dicts=history_dicts,
            user_profile=profile,
            diary_context=diary_context,
            warmth_label=warmth_label,
        )


