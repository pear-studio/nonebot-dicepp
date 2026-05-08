"""生活模拟器

驱动角色生活事件、主动消息调度、日记生成。
编排 CharacterLife、ProactiveScheduler、EventShareTaskQueue、DiaryGenerator。
"""
import asyncio
from typing import Optional, Dict, Any, List, TYPE_CHECKING
from dataclasses import dataclass
import random
from nonebot.log import logger
from ..data.store import PersonaDataStore
from ..data.models import RelationshipState, ScoreEvent
from ..character.models import Character
from ..game.decay import DecayCalculator
from ..wall_clock import persona_wall_now
from ..gateway.pipeline import make_segment
from .proactive_scheduler import ProactiveScheduler
from .event_share_queue import EventShareTaskQueue
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
    trace_enabled: bool = False
    trace_max_age_days: int = 7
    timezone: str = "Asia/Shanghai"

    @classmethod
    def from_persona(cls, persona: "PersonaConfig") -> "LifeConfig":
        return cls(
            proactive_event_share_threshold=persona.proactive_event_share_threshold,
            proactive_event_share_delay_min=persona.proactive_event_share_delay_min,
            proactive_event_share_delay_max=persona.proactive_event_share_delay_max,
            trace_enabled=persona.trace_enabled,
            trace_max_age_days=persona.trace_max_age_days,
            timezone=persona.timezone,
        )


class LifeSimulator:
    """生活模拟器 — 驱动角色生活事件、主动消息、日记生成"""

    def __init__(
        self,
        store: PersonaDataStore,
        character_life: CharacterLife,
        scheduler: ProactiveScheduler,
        event_share_queue: EventShareTaskQueue,
        diary_generator: DiaryGenerator,
        character: Character,
        config: LifeConfig,
        port: Optional[EventSharePort] = None,
        decay_calculator: Optional[DecayCalculator] = None,
    ):
        self.store = store
        self.character_life = character_life
        self.scheduler = scheduler
        self.event_share_queue = event_share_queue
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
                event_chain = await asyncio.wait_for(
                    self.character_life.tick(), timeout=300
                )
                if event_chain:
                    logger.info(
                        f"角色生活事件: {event_chain[0].get('description', '')[:50]}..."
                    )
                    best_event = max(event_chain, key=lambda e: e.get("share_desire", 0.0))
                    if (
                        self.event_share_queue
                        and best_event.get("share_desire", 0.0)
                        >= self.config.proactive_event_share_threshold
                    ):
                        delay = random.randint(
                            self.config.proactive_event_share_delay_min,
                            self.config.proactive_event_share_delay_max,
                        )
                        await self.event_share_queue.enqueue_event_share(
                            event_id=best_event.get("event_id", ""),
                            event_description=best_event.get("description", ""),
                            reaction=best_event.get("reaction", ""),
                            share_desire=best_event.get("share_desire", 0.0),
                            delay_minutes=delay,
                        )
            except asyncio.TimeoutError:
                logger.warning("tick: 角色生活事件生成超时（>300s），跳过本次以避免阻塞 proactive 系统")
            except Exception:
                logger.exception("tick: 角色生活事件生成失败")

        # 运行主动消息调度器
        if self.scheduler:
            try:
                proactive_msgs = await self.scheduler.tick()
                for msg in proactive_msgs:
                    await self._send_msg(msg)
            except Exception:
                logger.exception("tick: 主动消息调度失败")

        # 处理延迟队列中的事件分享
        if self.event_share_queue and self.scheduler:
            try:
                delayed_msgs = await self.event_share_queue.tick(
                    on_share=self._run_due_share
                )
                for msg in delayed_msgs:
                    await self._send_msg(msg)
            except Exception:
                logger.exception("tick: 延迟事件分享处理失败")

    async def _run_due_share(
        self, description: str, reaction: str, share_desire: float
    ) -> List[Dict]:
        """处理到期的事件分享任务"""
        return await self.scheduler.share_event_to_targets(
            description,
            reaction,
            self.scheduler.config.max_shares_per_event,
        )

    async def tick_daily(self) -> Optional[str]:
        """每日调用 — 清理 trace、关系衰减、生成日记"""
        try:
            await self._prune_traces()
            await self.apply_relationship_decay_batch()
            diary = await self.diary_generator.generate_diary()
            if diary:
                logger.info(f"生成日记: {len(diary)} 字")
            return diary
        except Exception as e:
            logger.exception(f"tick_daily 失败: {e}")
            return None

    async def apply_relationship_decay_batch(self) -> int:
        """每日批处理：将长时间未互动用户的时间衰减写入数据库。返回写库条数。"""
        if not self.decay_calculator or not self.character:
            return 0
        n = 0
        now = persona_wall_now(self.config.timezone)
        try:
            for rel in await self.store.list_all_relationships_raw():
                if not self.decay_calculator.should_apply_decay(rel, now):
                    continue
                deltas, reason = self.decay_calculator.calculate_decay(rel, now=now)
                rel.last_relationship_decay_applied_at = now
                if abs(deltas.intimacy) <= 0.01:
                    continue
                composite_before = rel.composite_score
                rel.apply_deltas(deltas, updated_at=now)
                await self.store.update_relationship(rel)
                await self.store.add_score_event(
                    ScoreEvent(
                        user_id=rel.user_id,
                        group_id=rel.group_id,
                        deltas=deltas,
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
            logger.warning(f"每日衰减批处理失败: {e}")
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

    async def _prune_traces(self) -> None:
        """清理过期 LLM trace"""
        if not self.config.trace_enabled:
            return
        try:
            deleted = await self.store.prune_llm_traces(self.config.trace_max_age_days)
            if deleted:
                logger.info(f"清理了 {deleted} 条过期 LLM trace")
        except Exception as e:
            logger.warning(f"清理 LLM trace 失败: {e}")

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
        await self.port.send_segmented(
            user_id,
            group_id,
            [make_segment(content, group_id)],
        )
