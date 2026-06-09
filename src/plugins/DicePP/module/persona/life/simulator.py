"""生活模拟器

驱动角色生活事件、主动消息调度、日记生成。
编排 CharacterLife、ProactiveScheduler、DiaryGenerator。
"""
import asyncio
import time
from typing import Optional, Dict, Any, List, TYPE_CHECKING
from dataclasses import dataclass
import random
from utils.logger import logger
from ..data.store import PersonaDataStore
from ..data.models import RelationshipState, ScoreEvent, MessageType
from ..character.models import Character
from ..game.decay import DecayCalculator
from utils.time import wall_now
from .proactive_scheduler import ProactiveScheduler
from .protocols import EventSharePort
from .diary import DiaryGenerator
from .character_life import CharacterLife


if TYPE_CHECKING:
    from core.config.pydantic_models import PersonaConfig


@dataclass
class LifeConfig:
    """生活域配置"""

    proactive_event_share_threshold: float = 0.4
    proactive_event_share_delay_min: int = 1
    proactive_event_share_delay_max: int = 5
    # 仅控制 LLMRouter 是否写入新 trace；不影响 run_cleanup 的清理行为
    trace_enabled: bool = False
    trace_max_age_days: int = 7
    score_history_max_age_days: int = 90
    scoring_failures_max_age_days: int = 30
    daily_events_keep_days: int = 30
    diary_keep_days: int = 30
    timezone: str = "Asia/Shanghai"

    @classmethod
    def from_persona(cls, persona: "PersonaConfig") -> "LifeConfig":
        return cls(
            proactive_event_share_threshold=persona.proactive_event_share_threshold,
            proactive_event_share_delay_min=persona.proactive_event_share_delay_min,
            proactive_event_share_delay_max=persona.proactive_event_share_delay_max,
            trace_enabled=persona.trace_enabled,
            trace_max_age_days=persona.trace_max_age_days,
            score_history_max_age_days=persona.score_history_max_age_days,
            scoring_failures_max_age_days=persona.scoring_failures_max_age_days,
            daily_events_keep_days=persona.daily_events_keep_days,
            diary_keep_days=persona.diary_keep_days,
            timezone=persona.timezone,
        )


class LifeSimulator:
    """生活模拟器 — 驱动角色生活事件、主动消息、日记生成"""

    def __init__(
        self,
        store: PersonaDataStore,
        character_life: CharacterLife,
        scheduler: ProactiveScheduler,
        diary_generator: DiaryGenerator,
        character: Character,
        config: LifeConfig,
        port: Optional[EventSharePort] = None,
        decay_calculator: Optional[DecayCalculator] = None,
    ):
        self.store = store
        self.character_life = character_life
        self.scheduler = scheduler
        self.diary_generator = diary_generator
        self.character = character
        self.config = config
        self.port = port
        self.decay_calculator = decay_calculator

    def update_character(self, character: Character) -> None:
        """同步新的角色卡引用到所有子组件"""
        self.character = character
        if self.character_life is not None:
            self.character_life.update_character(character)
        if self.scheduler is not None:
            self.scheduler.update_character(character)
        if self.diary_generator is not None:
            self.diary_generator.update_character(character)

    async def tick(self) -> None:
        """定时调用 — 驱动角色生活事件和主动消息调度"""
        # 尝试生成生活事件
        if self.character_life:
            try:
                t0 = time.monotonic()
                event_chain = await asyncio.wait_for(
                    self.character_life.tick(), timeout=300
                )
                elapsed_cl = time.monotonic() - t0
                if elapsed_cl > 60:
                    logger.warning(
                        f"tick: 角色生活事件生成耗时 {elapsed_cl:.1f}s (>60s)"
                    )
                if event_chain:
                    logger.info(
                        f"角色生活事件: {event_chain[0].get('description', '')[:50]}..."
                    )
                    self._schedule_share_from_chain(event_chain)
            except asyncio.TimeoutError:
                logger.warning("tick: 角色生活事件生成超时（>300s），跳过本次以避免阻塞 proactive 系统")
            except Exception:
                logger.exception("tick: 角色生活事件生成失败")

            # 消费自发事件待分享信息（drain 保证不丢并发注入的事件）
            for desc, reaction, share_desire in self.character_life.drain_pending_shares():
                if not self.scheduler:
                    break
                if share_desire >= self.config.proactive_event_share_threshold:
                    delay = random.randint(
                        self.config.proactive_event_share_delay_min,
                        self.config.proactive_event_share_delay_max,
                    )
                    self.scheduler.schedule_share(
                        event_id="",
                        event_description=desc,
                        reaction=reaction,
                        share_desire=share_desire,
                        delay_minutes=delay,
                    )

        # 运行主动消息调度器
        if self.scheduler:
            try:
                proactive_msgs = await self.scheduler.tick()
                # 串行 await 保证消息顺序，请勿改为 gather
                for msg in proactive_msgs:
                    await self._send_msg(msg)
            except Exception:
                logger.exception("tick: 主动消息调度失败")


    async def tick_daily(self) -> Optional[str]:
        """每日调用 — 清理 trace、关系衰减、生成日记"""
        try:
            await self._run_cleanup()
            await self.apply_relationship_decay_batch()
            diary = await self.diary_generator.generate_diary()
            if diary:
                logger.info(f"生成日记: {len(diary)} 字")
            return diary
        except Exception as e:
            logger.exception(f"tick_daily 失败: {e}")
            return None

    def _schedule_share_from_chain(self, event_chain: List[Dict[str, Any]]) -> None:
        """从事件链中选取分享欲望最高的事件，调度分享。"""
        if not self.scheduler:
            return
        best_event = max(event_chain, key=lambda e: e.get("share_desire", 0.0))
        if best_event.get("share_desire", 0.0) >= self.config.proactive_event_share_threshold:
            delay = random.randint(
                self.config.proactive_event_share_delay_min,
                self.config.proactive_event_share_delay_max,
            )
            self.scheduler.schedule_share(
                event_id=best_event.get("event_id", ""),
                event_description=best_event.get("description", ""),
                reaction=best_event.get("reaction", ""),
                share_desire=best_event.get("share_desire", 0.0),
                delay_minutes=delay,
            )

    async def apply_relationship_decay_batch(self) -> int:
        """每日批处理：将长时间未互动用户的时间衰减写入数据库。返回写库条数。"""
        if not self.decay_calculator or not self.character:
            return 0
        n = 0
        now = wall_now(self.config.timezone)
        try:
            for rel in await self.store.list_all_relationships_raw():
                if not self.decay_calculator.should_apply_decay(rel, now):
                    continue
                deltas, familiarity_delta, reason = self.decay_calculator.calculate_decay(rel, now=now)
                rel.last_relationship_decay_applied_at = now
                has_intimacy_decay = abs(deltas.intimacy) > 0.01
                has_fam_decay = abs(familiarity_delta) > 0.01
                if not has_intimacy_decay and not has_fam_decay:
                    continue
                composite_before = rel.composite_score
                if has_intimacy_decay:
                    rel.apply_deltas(deltas, updated_at=now)
                if has_fam_decay:
                    rel.apply_familiarity_delta(familiarity_delta, updated_at=now)
                await self.store.update_relationship(rel)
                await self.store.add_score_event(
                    ScoreEvent(
                        user_id=rel.user_id,
                        # 关系统一后衰减为全局行为，group_id 仅作审计（reason 字段已记录衰减标识）
                        group_id="",
                        deltas=deltas,
                        familiarity_delta=familiarity_delta,
                        composite_before=composite_before,
                        composite_after=rel.composite_score,
                        reason=f"time_decay_batch: {reason}",
                        conversation_digest="",
                    )
                )
                n += 1
            if n:
                logger.info(f"每日衰减批处理: 更新 {n} 条关系")
        except Exception as e:
            logger.warning(f"每日衰减批处理失败: {e}", exc_info=True)
        return n

    async def generate_daily_event(self) -> List[Dict[str, Any]]:
        """手动触发生活事件生成（用于调试）"""
        if not self.character_life:
            return []
        try:
            return await self.character_life.generate_daily_event()
        except Exception:
            logger.exception("手动生成事件失败")
            return []

    async def _run_cleanup(self) -> None:
        """统一数据清理（每日触发一次）。"""
        try:
            await self.store.run_cleanup(
                llm_traces_max_age_days=self.config.trace_max_age_days,
                score_history_max_age_days=self.config.score_history_max_age_days,
                scoring_failures_max_age_days=self.config.scoring_failures_max_age_days,
                daily_events_keep_days=self.config.daily_events_keep_days,
                diary_keep_days=self.config.diary_keep_days,
            )
        except Exception as e:
            logger.warning(f"数据清理失败: {e}", exc_info=True)

    async def _send_msg(self, msg: Dict[str, Any]) -> None:
        """通过 EventSharePort 发送单条消息"""
        if not self.port:
            logger.warning("MessagePort 未注入，消息无法发送")
            return
        user_id = msg.get("user_id", "")
        group_id = msg.get("group_id", "")
        content = msg.get("content", "")
        if not content:
            return
        if not user_id and not group_id:
            logger.warning(
                f"_send_msg 收件人为空，丢弃消息: content={content[:30]}..."
            )
            return
        effective_user_id = "assistant" if group_id else user_id
        msg_id = await self.store.add_message_stream(
            user_id=effective_user_id,
            group_id=group_id or "",
            role="assistant",
            type=MessageType.PROACTIVE,
            content=content,
            display_name="我",
        )
        if not await self.port.send(user_id, group_id, content, msg_id=msg_id):
            logger.warning(
                "_send_msg 发送失败: user_id=%s group_id=%s content=%s...",
                user_id,
                group_id,
                content[:30],
            )
