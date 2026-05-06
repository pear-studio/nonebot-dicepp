"""对话会话管理器

负责构造 LLM 上下文、调用 router、处理工具回调、执行评分。
通过依赖注入接收 store/router/tool_registry 等，零外部 import。

计费统一走 ``BillingPolicy.charge()``，由 ``_coordinator_on_result``
（中间轮）与 ``_chat_via_coordinator``（最终轮）两处调用，
避免注释不变量依赖人工维护。
"""
from typing import List, Dict, Optional, Any, Tuple, TYPE_CHECKING
from dataclasses import dataclass
import asyncio
import json
import logging
import time
import random
from collections import deque
from datetime import datetime, timedelta

from utils.string import estimate_tokens

from ..data.store import PersonaDataStore
from ..data.models import (
    ModelTier,
    UserProfile,
    RelationshipState,
    ScoreEvent,
    GroupConversation,
)
from ..llm.router import LLMRouter, QuotaExceeded
from ..character.models import Character
from ..chat.scoring import ScoringAgent
from ..chat.context import ContextBuilder
from ..game.decay import DecayCalculator
from ..wall_clock import persona_wall_now, PERSONA_EPOCH
from ..tools.registry import ToolRegistry, ToolDomain
from ..tools.context import ToolContext
from ..llm.coordinator import LLMCallCoordinator
from ..gateway.port import MessagePort
from ..gateway.pipeline import make_segment
from .billing import BillingPolicy

if TYPE_CHECKING:
    from core.config.pydantic_models import PersonaConfig

logger = logging.getLogger("persona.chat_session")


@dataclass
class ChatConfig:
    """对话域配置（从 PersonaConfig 中提取的子集）"""

    max_short_term_chars: int = 1500
    timezone: str = "Asia/Shanghai"
    lore_token_budget: int = 300
    tools_enabled: bool = True
    tools_max_rounds: int = 5
    relationship_refuse_enabled: bool = False
    relationship_refuse_prob_base: float = 0.3
    relationship_refuse_prob_max: float = 0.9
    scoring_interval: int = 10
    max_messages: int = 100
    group_max_age_minutes: int = 60
    group_context_budget_tokens: float = 2000.0
    group_max_messages: int = 15
    group_single_message_max_tokens: float = 500.0

    @classmethod
    def from_persona(cls, persona: "PersonaConfig") -> "ChatConfig":
        return cls(
            max_short_term_chars=persona.max_short_term_chars,
            timezone=persona.timezone,
            lore_token_budget=persona.lore_token_budget,
            tools_enabled=persona.tools_enabled,
            tools_max_rounds=persona.tools_max_rounds,
            relationship_refuse_enabled=persona.relationship_refuse_enabled,
            relationship_refuse_prob_base=persona.relationship_refuse_prob_base,
            relationship_refuse_prob_max=persona.relationship_refuse_prob_max,
            scoring_interval=persona.scoring_interval,
            max_messages=persona.max_messages,
            group_max_age_minutes=persona.group_max_age_minutes,
            group_context_budget_tokens=persona.group_context_budget_tokens,
            group_max_messages=persona.group_max_messages,
            group_single_message_max_tokens=persona.group_single_message_max_tokens,
        )


class ChatSession:
    """对话会话管理器 — 负责单轮/多轮对话、工具调用、评分、关系更新

    """

    DIGEST_MAX_MESSAGES = 6
    DIGEST_MAX_CHARS = 80

    def __init__(
        self,
        store: PersonaDataStore,
        router: LLMRouter,
        tool_registry: ToolRegistry,
        coordinator: LLMCallCoordinator,
        character: Character,
        config: ChatConfig,
        scoring_agent: ScoringAgent,
        context_builder: ContextBuilder,
        decay_calculator: Optional[DecayCalculator] = None,
        port: Optional[MessagePort] = None,
    ):
        self.store = store
        self.router = router
        self.tool_registry = tool_registry
        self.coordinator = coordinator
        self.character = character
        self.config = config
        self.scoring_agent = scoring_agent
        self.context_builder = context_builder
        self.decay_calculator = decay_calculator
        self.port = port
        self._pending_messages: Dict[str, deque] = {}
        self._last_messages: Dict[str, Tuple[str, float]] = {}
        self._billing = BillingPolicy(router)

    def update_character(self, character: Character) -> None:
        """同步新的角色卡引用"""
        self.character = character
        self.context_builder.update_character(character)

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
            if group_id:
                history = await self.store.get_group_conversations(group_id, limit=1)
            else:
                history = await self.store.get_recent_messages(user_id, group_id, limit=1)
            is_first = len(history) == 0

            if is_first and not group_id and self.character.first_mes:
                await self.store.add_message(user_id, group_id, "user", message)
                await self._persist_assistant_message(user_id, group_id, self.character.first_mes)
                return self.character.first_mes

            is_chat_message = not message.startswith(".") or message.lower().startswith(".ai")
            if self.config.relationship_refuse_enabled and not is_first and is_chat_message:
                rel = await self.store.get_relationship(user_id, group_id)
                if rel:
                    if self.decay_calculator:
                        initial = float(self.character.extensions.initial_relationship)
                        rel = self.decay_calculator.effective_relationship(rel, initial)
                    warmth_level, _ = rel.get_warmth_level(self.character.get_warmth_labels())
                    if warmth_level == 0:
                        score = rel.composite_score
                        base = self.config.relationship_refuse_prob_base
                        max_p = self.config.relationship_refuse_prob_max
                        p_refuse = base + (max_p - base) * (1 - score / 10)
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
                                    f"厌倦拒绝触发: user={user_id}, score={score:.2f}, "
                                    f"p_refuse={p_refuse:.2%}"
                                )
                                if not group_id:
                                    await self.store.add_message(user_id, group_id, "user", message)
                                    await self._persist_assistant_message(user_id, group_id, refuse_response)
                                else:
                                    await self.store.add_group_conversation(
                                        group_id=group_id,
                                        user_id=user_id,
                                        role="user",
                                        content=message,
                                        display_name=nickname or "",
                                    )
                                    await self._persist_assistant_message(user_id, group_id, refuse_response)
                                return refuse_response

            target_key = f"group:{group_id}" if group_id else f"user:{user_id}"

            if not group_id:
                await self.store.add_message(user_id, group_id, "user", message)

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

    # ── coordinator 回调 ──────────────────────────────────────

    async def _coordinator_chat_call_fn(
        self, user_id: str, group_id: str, messages: List[str]
    ) -> str:
        """coordinator chat 路径的单轮 LLM 调用。"""
        current_message = "\n".join(messages) if messages else ""

        messages_for_llm = await self._build_messages(user_id, group_id, current_message)

        if self.config.tools_enabled:
            logger.debug(f"对话走 tools 路径: user={user_id}, tools_enabled=true")
            response = await self._chat_with_tools(user_id, group_id, messages_for_llm)
        else:
            logger.debug(f"对话走普通路径: user={user_id}, tools_enabled=false")
            response = await self.router.generate(
                messages=messages_for_llm,
                model_tier=ModelTier.PRIMARY,
                user_id=user_id,
                group_id=group_id,
            )

        await self._persist_assistant_message(user_id, group_id, response)
        if not group_id:
            await self.store.prune_old_messages(user_id, group_id, self.config.max_messages)
        await self._update_interaction(user_id, group_id, current_message, response)
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
        await self._persist_assistant_message(user_id, group_id, fallback_response)
        await self._update_interaction(user_id, group_id, current_message, fallback_response)
        return fallback_response

    async def _coordinator_on_result(self, user_id: str, group_id: str, result: str) -> None:
        """中间轮结果：通过 MessagePort 发送、持久化并扣减额度。"""
        if self.port:
            await self.port.send_segmented(
                user_id,
                group_id,
                [make_segment(result, group_id)],
            )
        else:
            logger.warning(
                f"_coordinator_on_result: MessagePort 未注入，"
                f"消息无法发送 (user={user_id}, group={group_id})"
            )
        await self._persist_assistant_message(user_id, group_id, result)
        await self._billing.charge(user_id)

    async def _chat_via_coordinator(
        self, user_id: str, group_id: str, message: str, target_key: str
    ) -> Optional[str]:
        fallback_response: Optional[str] = None
        current_message_for_exhausted = message

        async def chat_call_fn(messages: List[str]) -> str:
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
            # 最终轮计费：on_result 只覆盖中间轮，最终轮通过 success 路径扣费。
            await self._billing.charge(user_id)
            return result.value
        return fallback_response if fallback_response is not None else None

    # ── 工具调用 ──────────────────────────────────────────────

    async def _chat_with_tools(
        self,
        user_id: str,
        group_id: str,
        messages: List[Dict],
    ) -> str:
        """支持工具调用的对话（通过 ToolRegistry）"""
        tools = self.tool_registry.get_definitions_for(ToolDomain.CHAT)
        ctx = ToolContext(user_id=user_id, group_id=group_id, store=self.store, send=self.port)
        tool_executor = self.tool_registry.make_executor_for(ToolDomain.CHAT, ctx=ctx)

        content, metadata = await self.router.generate_with_tools(
            messages=messages,
            tools=tools,
            tool_executor=tool_executor,
            model_tier=ModelTier.PRIMARY,
            max_tool_rounds=self.config.tools_max_rounds,
            user_id=user_id,
            group_id=group_id,
        )

        if metadata.get("tool_rounds", 0) > 0:
            logger.debug(
                f"工具调用完成: user={user_id}, "
                f"rounds={metadata.get('tool_rounds')}, "
                f"tools={metadata.get('tool_names')}, "
                f"cached={metadata.get('cached_tokens', 0)}"
            )

        return content

    # ── 关系与评分 ────────────────────────────────────────────

    async def _update_interaction(
        self, user_id: str, group_id: str, user_msg: str, assistant_msg: str
    ) -> None:
        rel = await self.store.get_relationship(user_id, group_id)
        initial = float(self.character.extensions.initial_relationship)
        if not rel:
            rel = await self.store.init_relationship(user_id, group_id, initial)

        now = persona_wall_now(self.config.timezone)
        decay_event: Optional[ScoreEvent] = None
        if self.decay_calculator and self.decay_calculator.should_apply_decay(rel, now):
            deltas, reason = self.decay_calculator.calculate_decay(rel, initial, now)
            if abs(deltas.intimacy) > 0.01:
                composite_before = rel.composite_score
                rel.apply_deltas(deltas, updated_at=now)
                decay_event = ScoreEvent(
                    user_id=user_id,
                    group_id=group_id,
                    deltas=deltas,
                    composite_before=composite_before,
                    composite_after=rel.composite_score,
                    reason=f"time_decay: {reason}",
                    conversation_digest="",
                )

        rel.last_interaction_at = now
        rel.last_relationship_decay_applied_at = now
        await self.store.update_relationship(rel)
        if decay_event:
            await self.store.add_score_event(decay_event)
            logger.info(
                f"应用时间衰减: {user_id} 衰减 {decay_event.deltas.intimacy:.2f}, 原因: {decay_event.reason}"
            )

        key = f"{user_id}:{group_id}"
        if key not in self._pending_messages:
            self._pending_messages[key] = deque(maxlen=100)
        self._pending_messages[key].append({"role": "user", "content": user_msg})
        self._pending_messages[key].append({"role": "assistant", "content": assistant_msg})

        if len(self._pending_messages[key]) >= self.config.scoring_interval * 2:
            try:
                await self._process_batch_scoring(user_id, group_id)
            except Exception as e:
                logger.warning(f"批量评分失败（不影响对话）: {e}")
                self._pending_messages.pop(key, None)

    async def _process_batch_scoring(self, user_id: str, group_id: str) -> None:
        if not self.scoring_agent:
            return

        key = f"{user_id}:{group_id}"
        messages = self._pending_messages.get(key, [])
        if not messages:
            return

        self._pending_messages[key] = []

        profile = await self.store.get_user_profile(user_id)
        rel = await self.store.get_relationship(user_id, group_id)

        rel_for_scoring = rel
        if rel and self.decay_calculator:
            initial = float(self.character.extensions.initial_relationship)
            rel_for_scoring = self.decay_calculator.effective_relationship(rel, initial)

        deltas, new_facts = await self.scoring_agent.batch_analyze(
            messages=messages,
            current_profile=profile,
            relationship=rel_for_scoring,
        )

        now = persona_wall_now(self.config.timezone)
        if rel:
            composite_before = rel.composite_score
            rel.apply_deltas(deltas, updated_at=now)
            await self.store.update_relationship(rel)

            event = ScoreEvent(
                user_id=user_id,
                group_id=group_id,
                deltas=deltas,
                composite_before=composite_before,
                composite_after=rel.composite_score,
                reason="批量评分",
                conversation_digest=self._build_conversation_digest(messages),
            )
            await self.store.add_score_event(event)

        if new_facts and profile:
            profile.merge_facts(new_facts, updated_at=now)
            await self.store.save_user_profile(profile)
        elif new_facts:
            new_profile = UserProfile(user_id=user_id, facts=new_facts)
            await self.store.save_user_profile(new_profile)

    @staticmethod
    def _build_conversation_digest(history: List[Dict[str, str]]) -> str:
        lines = []
        prefix_map = {"user": "U", "assistant": "A", "tool": "T", "system": "S"}
        for msg in history[-ChatSession.DIGEST_MAX_MESSAGES:]:
            prefix = prefix_map.get(msg.get("role"), "?")
            text = msg.get("content", "")
            if len(text) > ChatSession.DIGEST_MAX_CHARS:
                text = text[:ChatSession.DIGEST_MAX_CHARS - 3] + "..."
            lines.append(f"{prefix}: {text}")
        return "; ".join(lines)

    # ── 历史管理 ──────────────────────────────────────────────

    async def _fetch_short_term_history(
        self,
        user_id: str,
        group_id: str,
        limit: int = 15,
    ) -> Tuple[List[Dict[str, str]], bool]:
        """获取并格式化近期对话历史

        Returns: (history_dicts, was_truncated)
        """
        if not group_id:
            history = await self.store.get_recent_messages(user_id, group_id, limit=limit)
            history_dicts = [
                {"role": msg.role, "content": msg.content, "speaker_name": "你" if msg.role == "user" else "我"}
                for msg in history
            ]
            truncated = self.context_builder.truncate_by_turns(
                history_dicts, self.config.max_short_term_chars
            )
            return truncated, len(truncated) < len(history_dicts)

        history = await self.store.get_group_conversations(group_id, limit=None)
        return self._apply_token_window(history)

    def _apply_token_window(
        self,
        history: List[GroupConversation],
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
            formatted = f"[{speaker_name}] {content}"
            msg_cost = estimate_tokens(formatted)

            if total_tokens + msg_cost > budget and result:
                break

            result.insert(0, {
                "role": msg.role,
                "content": content,
                "speaker_name": speaker_name,
            })
            total_tokens += msg_cost

        return result, len(result) < original_count

    # ── 消息构建 ──────────────────────────────────────────────

    def _resolve_warmth_label(self, user_id: str, rel: Optional[RelationshipState]) -> str:
        """根据关系状态（含衰减计算）解析温暖度标签"""
        initial = float(self.character.extensions.initial_relationship)

        if rel:
            if self.decay_calculator:
                rel = self.decay_calculator.effective_relationship(rel, initial)
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
        max_diary_len = self.config.max_short_term_chars  # 复用同一配置

        events = await self.store.get_daily_events(today)
        if events:
            valid_events = [e for e in events if e.description and e.description.strip()]
            valid_events.sort(key=lambda e: e.created_at or PERSONA_EPOCH, reverse=True)

            if valid_events:
                descriptions = [e.description for e in valid_events]
                diary_context = "今天发生的事：" + "；".join(descriptions)
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
        current_message: str,
    ) -> Dict[str, List[str]]:
        """构建世界书 lore 段落"""
        return self.context_builder.build_lore_text(history_dicts, current_message)

    async def _build_messages(
        self,
        user_id: str,
        group_id: str,
        current_message: str,
    ) -> List[Dict[str, str]]:
        history_dicts, _ = await self._fetch_short_term_history(user_id, group_id)
        profile = await self.store.get_user_profile(user_id)
        rel = await self.store.get_relationship(user_id, group_id)
        warmth_label = self._resolve_warmth_label(user_id, rel)
        diary_context = await self._build_diary_context()
        lore_sections = self._build_lore_sections(history_dicts, current_message)

        debug_info = self.context_builder.build_debug_info(
            short_term_history=history_dicts,
            user_profile=profile,
            diary_context=diary_context,
            warmth_label=warmth_label,
            lore_sections=lore_sections,
        )
        logger.debug(f"context_debug: {debug_info}")

        return self.context_builder.build(
            short_term_history=history_dicts,
            user_profile=profile,
            diary_context=diary_context,
            current_message=current_message,
            warmth_label=warmth_label,
        )

    # ── 辅助 ──────────────────────────────────────────────────

    async def _persist_assistant_message(
        self, user_id: str, group_id: str, content: str, display_name: str = "我"
    ) -> None:
        if not group_id:
            await self.store.add_message(user_id, group_id, "assistant", content)
        else:
            await self.store.add_group_conversation(
                group_id=group_id,
                user_id="assistant",
                role="assistant",
                content=content,
                display_name=display_name,
            )

