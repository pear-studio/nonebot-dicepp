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
from ..character.models import Character
from .diary import DiaryGenerator
from .character_life import CharacterLife
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
    daily_events_keep_days: int = 30
    diary_keep_days: int = 30
    timezone: str = "Asia/Shanghai"

    @classmethod
    def from_persona(cls, persona: "PersonaConfig") -> "LifeConfig":
        return cls(
            trace_enabled=persona.trace_enabled,
            trace_max_age_days=persona.trace_max_age_days,
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
        diary_generator: DiaryGenerator,
        character: Character,
        config: LifeConfig,
        dm_agent: Optional["DMAgent"] = None,
        character_agent: Optional["CharacterAgent"] = None,
        sa_agent: Optional["SAAgent"] = None,
    ):
        self.store = store
        self.character_life = character_life
        self.diary_generator = diary_generator
        self.character = character
        self.config = config
        self.dm_agent = dm_agent
        self.character_agent = character_agent
        self.sa_agent = sa_agent

    def update_character(self, character: Character) -> None:
        """同步新的角色卡引用到所有子组件"""
        self.character = character
        if self.character_life is not None:
            self.character_life.update_character(character)
        if self.diary_generator is not None:
            self.diary_generator.update_character(character)

    async def tick(self) -> None:
        """定时调用 — 驱动角色生活事件"""
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
                logger.warning("tick: 角色生活事件生成超时（>300s），跳过本次")
            except Exception:
                logger.exception("tick: 角色生活事件生成失败")

    async def tick_daily(self) -> DailyTickResult:
        """每日调用 — 清理 trace、生成日记、Conversation compact。

        R9: DM/Character 的 compact_conversation 放在 finally 块，确保即使 cleanup、
        日记生成异常，日界 close 也会执行，避免 Conversation 跨虚构日泄漏。
        """
        try:
            await self._run_cleanup()
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
                daily_events_keep_days=self.config.daily_events_keep_days,
                diary_keep_days=self.config.diary_keep_days,
            )
        except Exception as e:
            logger.warning(f"数据清理失败: {e}", exc_info=True)
