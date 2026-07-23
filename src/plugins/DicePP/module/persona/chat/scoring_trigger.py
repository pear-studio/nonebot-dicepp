"""ScoringTrigger — 评分触发器

管理 pending 消息收集、批量评分触发、关系衰减应用、familiarity 即时结算。
从 ChatSession._update_interaction / _process_batch_scoring 迁移而来。

CH4 修复: pending key 改为 (user_id, group_id)，跨群消息隔离。
CH6 修复: 统一异常/parse_error 重试逻辑，SCORING_MAX_RETRIES = 3。
"""
import math
from collections import deque
from typing import Dict, Tuple, Optional, List, Any
from plugins.DicePP.utils.logger import logger

from ..data.store import PersonaDataStore
from ..data.models import (
    UserProfile,
    RelationshipState,
    ScoreEvent,
    ScoringFailure,
    ScoreDeltas,
)
from ..character.models import Character
from ..chat.scoring import ScoringAgent
from ..game.decay import DecayCalculator
from plugins.DicePP.utils.time import wall_now
from .chat_config import ChatConfig

# familiarity 即时结算参数（硬编码，暂无角色间差异化需求）
_FAMILIARITY_PER_INTERACTION = 0.6   # 每次聊天互动增量
_FAMILIARITY_DAILY_CAP = 15.0       # 单日 familiarity 上限


class ScoringTrigger:
    """评分触发器 — 管理 pending 消息收集、批量评分触发、关系衰减

    公开方法:
        on_interaction: 完整替代原 _update_interaction
        effective_relationship: 暴露供 ChatSession 门控使用
        update_character: 同步内部 character 引用
    """

    SCORING_MAX_RETRIES = 3
    DIGEST_MAX_MESSAGES = 6
    DIGEST_MAX_CHARS = 80

    def __init__(
        self,
        store: PersonaDataStore,
        scoring_agent: Optional[ScoringAgent],
        decay_calculator: Optional[DecayCalculator],
        character: Character,
        config: ChatConfig,
    ):
        self._store = store
        self._scoring_agent = scoring_agent
        self._decay_calculator = decay_calculator
        self._character = character
        self._config = config

        # (user_id, group_id) -> deque of pending messages (CH4 fix)
        self._pending_messages: Dict[Tuple[str, str], deque] = {}
        # (user_id, group_id) -> retry count (CH6 fix)
        self._retry_count: Dict[Tuple[str, str], int] = {}

        # warn_pending 追踪: user_id -> True
        self._warn_pending: Dict[str, bool] = {}

    # ── 公开 API ──────────────────────────────────────────────

    def update_character(self, character: Character) -> None:
        """同步内部 character 引用"""
        self._character = character

    def effective_relationship(self, rel: RelationshipState) -> RelationshipState:
        """应用衰减计算后返回有效关系状态"""
        if self._decay_calculator:
            return self._decay_calculator.effective_relationship(rel)
        return rel

    async def on_interaction(
        self,
        user_id: str,
        group_id: str,
        user_msg: str,
        assistant_msg: str,
    ) -> None:
        """处理一次对话交互：衰减 + familiarity 即时结算 + reputation 恢复 + pending 收集 + 批量评分触发

        完整替代原 ChatSession._update_interaction。调用者需保证
        user_msg/assistant_msg 已持久化。评分失败不阻塞对话流程。
        """
        # ── 1. 获取/初始化关系 ──────────────────────────────
        rel = await self._store.get_relationship(user_id)
        if not rel:
            rel = await self._store.init_relationship(user_id)

        now = wall_now(self._config.timezone)

        # ── reputation 每日恢复（延迟持久化，由后续 update_relationship 统一写入）──
        if rel:
            await self._store.try_daily_reputation_recovery(rel, now, persist=False)

        # ── 2. 应用时间衰减 ────────────────────────────────
        decay_event: Optional[ScoreEvent] = None
        if self._decay_calculator and self._decay_calculator.should_apply_decay(rel, now):
            deltas, familiarity_delta, reason = self._decay_calculator.calculate_decay(rel, now=now)
            has_intimacy_decay = abs(deltas.intimacy) > 0.01
            has_fam_decay = abs(familiarity_delta) > 0.01
            if has_intimacy_decay or has_fam_decay:
                composite_before = rel.composite_score
                if has_intimacy_decay:
                    rel.apply_deltas(deltas, updated_at=now)
                if has_fam_decay:
                    rel.apply_familiarity_delta(familiarity_delta, updated_at=now)
                decay_event = ScoreEvent(
                    user_id=user_id,
                    group_id=group_id,
                    deltas=deltas,
                    familiarity_delta=familiarity_delta,
                    composite_before=composite_before,
                    composite_after=rel.composite_score,
                    reason=f"time_decay: {reason}",
                    conversation_digest="",
                )

        # ── 3. familiarity 即时结算（聊天轮次，持久化到 DB）──
        today = now.strftime("%Y-%m-%d")
        daily_total = await self._store.get_familiarity_daily(user_id, today)
        if daily_total < _FAMILIARITY_DAILY_CAP:
            fam_round_delta = min(_FAMILIARITY_PER_INTERACTION, _FAMILIARITY_DAILY_CAP - daily_total)
            new_total = await self._store.add_familiarity_daily(user_id, today, fam_round_delta)
            # 基于 DB 返回的实际 total 计算增量，消除 read-modify-write 竞态
            actual_delta = new_total - daily_total
            if actual_delta > 0:
                rel.apply_familiarity_delta(actual_delta, updated_at=now)

        rel.last_interaction_at = now
        rel.last_miss_sent_at = None  # 用户回应后关闭衰减开关
        rel.last_relationship_decay_applied_at = None  # 下次衰减从新互动起算
        await self._store.update_relationship(rel)
        if decay_event:
            await self._store.add_score_event(decay_event)
            logger.info(
                f"应用时间衰减: {user_id} intimacy={deltas.intimacy:.2f}, "
                f"familiarity={decay_event.familiarity_delta:.2f}, "
                f"原因: {decay_event.reason}"
            )

        # ── 4. 收集 pending 消息 ───────────────────────────
        # CH4 fix: key 为 (user_id, group_id)，跨群隔离
        key = (user_id, group_id)
        if key not in self._pending_messages:
            self._pending_messages[key] = deque(maxlen=100)
        self._pending_messages[key].append({
            "role": "user", "content": user_msg, "created_at": now,
        })
        self._pending_messages[key].append({
            "role": "assistant", "content": assistant_msg, "created_at": now,
        })

        # ── 5. 达到阈值 → 触发批量评分 ─────────────────────
        if len(self._pending_messages[key]) >= self._config.scoring_interval * 2:
            try:
                await self._process_batch_scoring(user_id, group_id)
            except Exception:
                logger.exception(
                    f"on_interaction 非预期异常（不影响对话）: "
                    f"user={user_id}, group={group_id}"
                )
                # 不弹出 pending，等待下次触发 (CH6 fix)

    # ── 批量评分 ────────────────────────────────────────────

    async def _process_batch_scoring(
        self, user_id: str, group_id: str
    ) -> None:
        """执行批量评分 + 结果写入

        CH6 fix: 不再 re-raise。异常/parse_error 统一走重试逻辑。
        """
        if not self._scoring_agent:
            return

        key = (user_id, group_id)
        messages = list(self._pending_messages.get(key, []))
        if not messages:
            return

        messages_count = len(messages)

        profile = await self._store.get_user_profile(user_id)
        rel = await self._store.get_relationship(user_id)

        rel_for_scoring = rel
        if rel and self._decay_calculator:
            rel_for_scoring = self._decay_calculator.effective_relationship(rel)

        try:
            result = await self._scoring_agent.batch_analyze(
                messages=messages,
                current_profile=profile,
                relationship=rel_for_scoring,
                user_id=user_id,
                group_id=group_id,
                warn_pending=self._warn_pending.get(user_id, False),
            )
        except Exception as exc:
            await self._store.record_scoring_failure(
                ScoringFailure(
                    user_id=user_id,
                    group_id=group_id,
                    messages_count=messages_count,
                    error=f"{type(exc).__name__}: {exc}",
                    conversation_digest=self._build_conversation_digest(messages),
                )
            )
            self._handle_scoring_failure(key)
            return

        if result.parse_error:
            logger.warning(
                f"评分解析失败，{messages_count} 条消息保留待重试: "
                f"user={user_id}, parse_error={result.parse_error[:100]}"
            )
            await self._store.record_scoring_failure(
                ScoringFailure(
                    user_id=user_id,
                    group_id=group_id,
                    messages_count=messages_count,
                    error=result.parse_error,
                    raw_response=result.raw_response,
                    conversation_digest=self._build_conversation_digest(messages),
                )
            )
            self._handle_scoring_failure(key)
            return

        # ── 评分成功：清除 pending 和 retry ────────────────
        self._pending_messages.pop(key, None)
        self._retry_count.pop(key, None)

        # ── 应用评分结果 ──────────────────────────────────
        deltas = result.deltas
        new_facts = result.facts
        now = wall_now(self._config.timezone)

        # warn_pending 状态更新
        if deltas.reputation_delta < 0:
            # 已扣分 → 清除 warn_pending
            self._warn_pending.pop(user_id, None)
        elif deltas.warning_issued:
            # LLM 已发出警告但未扣分 → 设置 warn_pending，下次可扣分
            self._warn_pending[user_id] = True
        else:
            # 正常互动 → 清除 warn_pending
            self._warn_pending.pop(user_id, None)

        if rel:
            composite_before = rel.composite_score
            rel.apply_deltas(deltas, updated_at=now)
            await self._store.update_relationship(rel)

            event = ScoreEvent(
                user_id=user_id,
                group_id=group_id,
                deltas=deltas,
                familiarity_delta=0.0,  # familiarity 已在 on_interaction 即时结算
                composite_before=composite_before,
                composite_after=rel.composite_score,
                reason="批量评分",
                conversation_digest=self._build_conversation_digest(messages),
            )
            await self._store.add_score_event(event)

        if new_facts and profile:
            profile.merge_facts(new_facts, updated_at=now)
            await self._store.save_user_profile(profile)
        elif new_facts:
            new_profile = UserProfile(user_id=user_id, facts=new_facts)
            await self._store.save_user_profile(new_profile)

    # ── 重试逻辑 (CH6) ─────────────────────────────────────

    def _handle_scoring_failure(self, key: Tuple[str, str]) -> None:
        """统一异常/parse_error 重试处理

        重试次数 < SCORING_MAX_RETRIES - 1: 递增计数，保留 pending
        重试次数 >= SCORING_MAX_RETRIES - 1: 丢弃 pending，清除计数
        """
        retry_count = self._retry_count.get(key, 0)
        if retry_count >= self.SCORING_MAX_RETRIES - 1:
            # 超过上限：丢弃
            self._pending_messages.pop(key, None)
            self._retry_count.pop(key, None)
            logger.warning(
                f"评分连续失败 {self.SCORING_MAX_RETRIES} 次，"
                f"丢弃 pending 消息: key={key}"
            )
        else:
            self._retry_count[key] = retry_count + 1
            logger.info(
                f"评分失败（第 {retry_count + 1} 次），保留 pending 待重试: "
                f"key={key}"
            )

    # ── 工具方法 ────────────────────────────────────────────

    @staticmethod
    def _build_conversation_digest(history: List[Dict[str, str]]) -> str:
        """构建对话内容摘要（用于审计记录）"""
        lines = []
        prefix_map = {"user": "U", "assistant": "A", "tool": "T", "system": "S"}
        for msg in history[-ScoringTrigger.DIGEST_MAX_MESSAGES:]:
            prefix = prefix_map.get(msg.get("role"), "?")
            text = msg.get("content", "")
            if len(text) > ScoringTrigger.DIGEST_MAX_CHARS:
                text = text[:ScoringTrigger.DIGEST_MAX_CHARS - 3] + "..."
            lines.append(f"{prefix}: {text}")
        return "; ".join(lines)
