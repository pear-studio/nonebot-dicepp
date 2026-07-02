"""生活模拟器

Phase 1: 持有 DM / Character / SA 三个 Agent 实例，由 LifeSimulator 编排。
CharacterLife 退回纯状态管理（槽位触发、数值加减）。
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
from .proactive_scheduler import ProactiveScheduler
from .protocols import EventSharePort
from .diary import DiaryGenerator
from .character_life import CharacterLife

if TYPE_CHECKING:
    from core.config.pydantic_models import PersonaConfig
    from .dm_agent import DMAgent
    from .character_agent import CharacterAgent
    from .sa_agent import SAAgent


@dataclass
class LifeConfig:
    """生活域配置"""

    proactive_event_share_threshold: float = 0.4
    proactive_event_share_delay_min: int = 1
    proactive_event_share_delay_max: int = 5
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
    """生活模拟器 — Phase 1: 持有 DM / Character / SA Agent，编排协作"""

    def __init__(
        self,
        store: PersonaDataStore,
        character_life: CharacterLife,
        scheduler: ProactiveScheduler,
        diary_generator: DiaryGenerator,
        character: Character,
        config: LifeConfig,
        dm_agent: Optional["DMAgent"] = None,
        character_agent: Optional["CharacterAgent"] = None,
        sa_agent: Optional["SAAgent"] = None,
        port: Optional[EventSharePort] = None,
        decay_calculator: Optional[DecayCalculator] = None,
    ):
        self.store = store
        self.character_life = character_life
        self.scheduler = scheduler
        self.diary_generator = diary_generator
        self.character = character
        self.config = config
        self.dm_agent = dm_agent
        self.character_agent = character_agent
        self.sa_agent = sa_agent
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

    async def tick_daily(self) -> Optional[str]:
        """每日调用 — 清理 trace、关系衰减、生成日记、SA 规划、Conversation compact"""
        try:
            await self._run_cleanup()
            await self.apply_relationship_decay_batch()
            diary = await self.diary_generator.generate_diary()

            # 日终 compact Conversation（截断，释放 cache 资源）
            for name, agent in [("DM", self.dm_agent), ("Character", self.character_agent)]:
                if agent:
                    try:
                        await agent.compact_conversation()
                    except Exception:
                        logger.warning(f"{name} compact_conversation 失败", exc_info=True)

            # Phase 1: SA Agent 规划
            if diary and self.sa_agent:
                try:
                    await self._run_sa_planning()
                except Exception as e:
                    logger.warning(f"SA 规划失败: {e}", exc_info=True)

            if diary:
                logger.info(f"生成日记: {len(diary)} 字")
            return diary
        except Exception as e:
            logger.exception(f"tick_daily 失败: {e}")
            return None

    async def _run_sa_planning(self) -> None:
        """执行 SA 叙事规划"""
        # 收集素材
        today = self.character_life._get_today_str()
        diary_text = await self.store.get_diary(today) or "（无）"
        today_events = await self.store.get_daily_events(today)
        events_text = "\n".join(
            f"- {e.description} ({e.reaction})"
            for e in today_events[-10:]
        ) if today_events else "（无）"

        # 检查 story_deck 是否为空
        story_deck_count = await self.store.get_story_deck_count()
        story_deck_is_empty = story_deck_count == 0

        sa_context = {
            "character_name": self.character.name,
            "character_description": self.character.description,
            "world": self.character.extensions.world,
            "diary_text": diary_text,
            "events_text": events_text,
            "story_deck_is_empty": story_deck_is_empty,
        }

        result = await self.sa_agent.plan(sa_context)
        if result.success:
            logger.info("SA 叙事规划完成")
        else:
            logger.warning(f"SA 叙事规划失败: {result.error}")

    async def apply_relationship_decay_batch(self) -> int:
        """每日批处理"""
        if not self.decay_calculator or not self.character:
            return 0
        n = 0
        from utils.time import get_clock
        now = get_clock().now()
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
