"""日记生成器

从 CharacterLife 中提取日记生成逻辑，独立为 DiaryGenerator。
"""
from typing import Optional, List
from dataclasses import dataclass
from utils.logger import logger
from datetime import timedelta

from ..data.store import PersonaDataStore
from ..character.models import Character
from ..life.event_agent import EventGenerationAgent
from utils.time import wall_now


@dataclass
class DiaryConfig:
    """日记生成配置"""

    diary_time: str = "23:30"
    timezone: str = "Asia/Shanghai"


class DiaryGenerator:
    """日记生成器 — 负责获取当日事件、调用 LLM 生成日记、保存与清理"""

    def __init__(
        self,
        store: PersonaDataStore,
        event_agent: EventGenerationAgent,
        character: Character,
        config: DiaryConfig,
    ):
        self.store = store
        self.event_agent = event_agent
        self.character_name = character.name
        self.character_description = character.description
        self.config = config

    def update_character(self, character: Character) -> None:
        """同步新的角色卡信息"""
        self.character_name = character.name
        self.character_description = character.description

    async def generate_diary(self) -> Optional[str]:
        """
        生成日记。

        根据当前时间与 diary_time 的关系判断该取哪天的事件：
        - 当前时间 >= diary_time：今天已结束，取当天事件生成当天日记
        - 当前时间 < diary_time：今天刚开始，取昨天事件生成昨天日记

        Returns:
            日记内容，如果失败则返回 None
        """
        try:
            now = wall_now(self.config.timezone)

            diary_hour, diary_minute = map(int, self.config.diary_time.split(":"))
            diary_minutes = diary_hour * 60 + diary_minute
            now_minutes = now.hour * 60 + now.minute

            if now_minutes >= diary_minutes:
                diary_date = now.strftime("%Y-%m-%d")
                prev_date = (now - timedelta(days=1)).strftime("%Y-%m-%d")
            else:
                diary_date = (now - timedelta(days=1)).strftime("%Y-%m-%d")
                prev_date = (now - timedelta(days=2)).strftime("%Y-%m-%d")

            events = await self.store.get_daily_events(diary_date)
            if not events:
                logger.debug("没有事件，跳过日记生成")
                return None

            prev_diary = await self.store.get_diary(prev_date)

            character_state = await self.store.get_character_state()

            events_dict = []
            for e in events:
                evt_time = e.created_at.strftime("%H:%M") if e.created_at else "??:??"
                events_dict.append({
                    "description": e.description,
                    "context_summary": e.context_summary,
                    "reaction": e.reaction,
                    "time": evt_time,
                })

            diary_content = await self.event_agent.generate_diary(
                events=events_dict,
                character_name=self.character_name,
                character_description=self.character_description,
                yesterday_diary=prev_diary,
                energy=character_state.energy if character_state else None,
                mood=character_state.mood if character_state else None,
                health=character_state.health if character_state else None,
                current_intention=character_state.current_intention if character_state else None,
            )

            await self.store.save_diary(diary_date, diary_content)

            logger.info(f"生成日记: {len(diary_content)} 字")
            return diary_content

        except Exception as e:
            logger.exception(f"生成日记失败: {e}")
            return None

