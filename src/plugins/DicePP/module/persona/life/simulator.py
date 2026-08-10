"""生活模拟器

Phase 1: 持有 DM / Character / SA 三个 Agent 实例，由 LifeSimulator 编排。
CharacterLife 退回纯状态管理（槽位触发、数值加减）。
"""
import asyncio
import time
import uuid
from typing import Optional, Dict, Any, List, TYPE_CHECKING
from dataclasses import dataclass
import random
from plugins.DicePP.utils.logger import logger
from ..data.store import PersonaDataStore
from ..data.models import RelationshipState, ScoreEvent, MessageType
from ..character.models import Character
from ..game.decay import DecayCalculator
from .proactive_scheduler import ProactiveScheduler
from .protocols import EventSharePort
from .diary import DiaryGenerator
from .character_life import CharacterLife
from .conversation_scope import ConversationScope
from .share_scheduler import ShareScheduler
from .types import DailyTickResult

if TYPE_CHECKING:
    from plugins.DicePP.core.config.pydantic_models import PersonaConfig
    from .dm_agent import DMAgent
    from .character_agent import CharacterAgent
    from .sa_agent import SAAgent


@dataclass
class LifeConfig:
    """生活域配置"""

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
        chat_registry: Optional[Any] = None,
        share_scheduler: Optional[ShareScheduler] = None,
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
        self.chat_registry = chat_registry
        self.share_scheduler = share_scheduler

    def update_character(self, character: Character) -> None:
        """同步新的角色卡引用到所有子组件"""
        self.character = character
        if self.character_life is not None:
            self.character_life.update_character(character)
        if self.scheduler is not None:
            self.scheduler.update_character(character)
        if self.diary_generator is not None:
            self.diary_generator.update_character(character)
        if self.share_scheduler is not None:
            self.share_scheduler.update_character(character)

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

        # 分享日程
        if self.share_scheduler:
            try:
                await self.share_scheduler.tick()
            except Exception:
                logger.exception("share_scheduler.tick 失败")

        # 运行主动消息调度器
        if self.scheduler:
            try:
                proactive_msgs = await self.scheduler.tick()
                for msg in proactive_msgs:
                    await self._send_msg(msg)
            except Exception:
                logger.exception("tick: 主动消息调度失败")

    async def tick_daily(self) -> DailyTickResult:
        """每日调用 — 清理 trace、关系衰减、生成日记、Conversation compact。

        R9: DM/Character 的 compact_conversation 放在 finally 块，确保即使 cleanup、
        衰减或日记生成异常，日界 close 也会执行，避免 Conversation 跨虚构日泄漏。
        """
        try:
            await self._run_cleanup()
            await self.apply_relationship_decay_batch()
            result = await self.diary_generator.generate_diary()

            if result.diary:
                logger.info(f"生成日记: {len(result.diary)} 字")
            return result
        except Exception as e:
            logger.exception(f"tick_daily 失败: {e}")
            return DailyTickResult()
        finally:
            # 日界 close 必须在 finally 执行——无论前序步骤是否异常，
            # DM/Character 的 Conversation 都必须关闭，防止跨虚构日复用。
            for name, agent in [("DM", self.dm_agent), ("Character", self.character_agent)]:
                if agent:
                    try:
                        await agent.compact_conversation()
                    except Exception:
                        logger.warning(
                            f"tick_daily finally: {name} compact_conversation 失败",
                            exc_info=True,
                        )

    async def run_daily_planning(self, diary: str, diary_date: str) -> None:
        """在日报关键路径之外执行当日日记对应的 SA 规划。"""
        if not diary or not self.sa_agent:
            return
        await self._run_sa_planning(diary, diary_date)

    async def _run_sa_planning(self, diary_text: str, diary_date: str) -> None:
        """使用显式日记日期执行 SA 叙事规划，避免后台延迟导致日期漂移。"""
        today_events = await self.store.get_daily_events(diary_date)
        events_text = "\n".join(
            f"- {e.description} ({e.reaction})"
            for e in today_events[-10:]
        ) if today_events else "（无）"

        # 检查 story_deck 是否为空
        story_deck_count = await self.store.get_story_deck_count()
        story_deck_is_empty = story_deck_count == 0

        interaction_id = uuid.uuid4().hex

        sa_context = {
            "character_name": self.character.name,
            "character_description": self.character.description,
            "world": self.character.extensions.world,
            "diary_text": diary_text,
            "events_text": events_text,
            "story_deck_is_empty": story_deck_is_empty,
        }

        result = await self.sa_agent.run(sa_context, interaction_id=interaction_id)
        if result.success:
            logger.info("SA 叙事规划完成")
        else:
            logger.warning(f"SA 叙事规划失败: {result.error}")

    async def apply_relationship_decay_batch(self) -> int:
        """每日批处理"""
        if not self.decay_calculator or not self.character:
            return 0
        n = 0
        from plugins.DicePP.utils.time import get_clock
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
        """发送主动消息。

        R8: 先投递消息（port.send），成功后再写入 message_stream 和回流到 Chat
        Conversation。投递失败则不落任何记录，确保历史中只出现实际送达的消息。
        回流使用 append_visible（非 append_visible_if_active）：无 active session
        时创建轻量 Conversation 记录，有 active 时走静默轮换 + append 语义。
        """
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

        # R8(a): 先投递（发送方自行维护 stream），成功后再落记录
        if not await self.port.send(
            user_id,
            group_id,
            content,
            skip_history_record=True,
            message_type=MessageType.PROACTIVE,
        ):
            logger.warning(
                "_send_msg 发送失败: user_id=%s group_id=%s content=%s...",
                user_id, group_id, content[:30],
            )
            return

        # 投递成功：写入 message_stream（历史权威记录）
        effective_user_id = "assistant" if group_id else user_id
        msg_id = await self.store.add_message_stream(
            user_id=effective_user_id,
            group_id=group_id or "",
            role="assistant",
            type=MessageType.PROACTIVE,
            content=content,
            display_name=self.character.name,
        )

        # R8(b)(c): 回流到 Chat Conversation——使用 append_visible 统一语义，
        # 无 active session 时创建轻量 Conversation（无 ChatAgent），有 active
        # 时走静默轮换 + append。静默过期检测由 append_visible 内部处理。
        if self.chat_registry is not None:
            try:
                scope = (
                    ConversationScope.for_group(group_id)
                    if group_id
                    else ConversationScope.for_private(user_id)
                )
                await self.chat_registry.append_visible(
                    scope, msg_id, "assistant",
                )
            except Exception:
                logger.warning(
                    "主动消息回流失败: user_id=%s group_id=%s",
                    user_id, group_id, exc_info=True,
                )
