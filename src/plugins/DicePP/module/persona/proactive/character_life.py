"""
角色生活模拟

管理角色的全天生活事件生成和日记记录
"""
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta
import random
import logging

from ..agents.event_agent import EventGenerationAgent, EventContext
from ..character.models import Character
from ..data.store import PersonaDataStore

logger = logging.getLogger("persona.character_life")


class CharacterLifeConfig:
    """角色生活模拟配置"""

    def __init__(
        self,
        enabled: bool = True,
        event_hours: List[int] = None,
        jitter_minutes: int = 15,
        diary_time: str = "23:30",
    ):
        self.enabled = enabled
        self.event_hours = event_hours or [8, 11, 14, 17, 20]
        self.jitter_minutes = jitter_minutes
        self.diary_time = diary_time  # HH:MM format


class CharacterLife:
    """角色生活管理器"""

    def __init__(
        self,
        config: CharacterLifeConfig,
        event_agent: EventGenerationAgent,
        data_store: PersonaDataStore,
        character: Character,
    ):
        self.config = config
        self.event_agent = event_agent
        self.data_store = data_store
        self.character = character
        self._generated_hours: set = set()  # 今天已经生成事件的小时
        self._last_event_date: Optional[str] = None

    def _get_today_str(self) -> str:
        return datetime.now().strftime("%Y-%m-%d")

    def _reset_daily_state(self) -> None:
        """重置每日状态"""
        today = self._get_today_str()
        if self._last_event_date != today:
            self._generated_hours.clear()
            self._last_event_date = today
            logger.debug(f"重置每日事件状态: {today}")

    async def tick(self) -> Optional[Dict[str, str]]:
        """
        检查是否需要生成事件

        Returns:
            生成的事件和反应，如果没有则返回 None
        """
        if not self.config.enabled:
            return None

        self._reset_daily_state()

        now = datetime.now()
        current_hour = now.hour

        # 检查当前小时是否在配置的事件时间中
        if current_hour not in self.config.event_hours:
            return None

        # 检查是否已经生成过该小时的事件
        if current_hour in self._generated_hours:
            return None

        # 添加随机抖动，避免整点生成
        jitter = random.randint(-self.config.jitter_minutes, self.config.jitter_minutes)
        target_minute = 30 + jitter  # 默认在半点前后

        if now.minute < target_minute - 5 or now.minute > target_minute + 5:
            return None

        # 生成事件
        event_data = await self._generate_daily_event()
        if event_data:
            self._generated_hours.add(current_hour)
            return event_data

        return None

    async def _generate_daily_event(self) -> Optional[Dict[str, str]]:
        """生成每日生活事件"""
        try:
            today = self._get_today_str()

            # 获取上下文数据
            recent_diaries = await self._get_recent_diaries(3)
            today_events = await self._get_today_events()
            permanent_state = await self.data_store.get_character_state()

            # 构建上下文
            context = EventContext(
                character_name=self.character.name,
                character_description=self.character.description,
                world=self.character.extensions.world,
                scenario=self.character.extensions.scenario,
                recent_diaries=recent_diaries,
                today_events=[{"description": e.description} for e in today_events],
                permanent_state=permanent_state,
            )

            # 生成事件
            event_desc = await self.event_agent.generate_event(context)

            # 生成反应
            reaction = await self.event_agent.generate_reaction(
                event=event_desc,
                character_name=self.character.name,
                character_description=self.character.description,
            )

            # 保存到数据库
            await self.data_store.add_daily_event(
                date=today,
                event_type="scheduled",
                description=event_desc,
                reaction=reaction,
            )

            logger.info(f"生成生活事件: {event_desc[:50]}...")

            return {
                "description": event_desc,
                "reaction": reaction,
                "time": datetime.now().strftime("%H:%M"),
            }

        except Exception as e:
            logger.exception(f"生成生活事件失败: {e}")
            return None

    async def generate_diary(self) -> Optional[str]:
        """
        生成今天的日记

        Returns:
            日记内容，如果失败则返回 None
        """
        if not self.config.enabled:
            return None

        try:
            today = self._get_today_str()

            # 获取今天的事件
            events = await self._get_today_events()
            if not events:
                logger.debug("今天没有事件，跳过日记生成")
                return None

            # 获取昨天的日记作为上下文
            yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
            yesterday_diary = await self.data_store.get_diary(yesterday)

            # 转换为字典格式
            events_dict = [
                {"description": e.description, "reaction": e.reaction}
                for e in events
            ]

            # 生成日记
            diary_content = await self.event_agent.generate_diary(
                events=events_dict,
                character_name=self.character.name,
                character_description=self.character.description,
                yesterday_diary=yesterday_diary,
            )

            # 保存日记
            await self.data_store.save_diary(today, diary_content)

            # 清理今天的事件
            await self.data_store.clear_daily_events(today)

            # 清理旧日记（只保留30天）
            await self._prune_old_diaries(30)

            logger.info(f"生成日记: {len(diary_content)} 字")
            return diary_content

        except Exception as e:
            logger.exception(f"生成日记失败: {e}")
            return None

    async def _get_recent_diaries(self, days: int) -> List[str]:
        """获取最近 N 天的日记"""
        diaries = []
        for i in range(1, days + 1):
            date = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
            diary = await self.data_store.get_diary(date)
            if diary:
                diaries.append(diary)
        return diaries

    async def _get_today_events(self) -> List[Any]:
        """获取今天的事件"""
        today = self._get_today_str()
        return await self.data_store.get_daily_events(today)

    async def _prune_old_diaries(self, keep_days: int) -> None:
        """清理旧日记"""
        try:
            deleted = await self.data_store.prune_diaries(keep_days)
            if deleted > 0:
                logger.info(f"清理了 {deleted} 条旧日记")
        except Exception as e:
            logger.warning(f"清理旧日记失败: {e}")

    def get_event_status(self) -> Dict[str, Any]:
        """获取事件生成状态（用于调试）"""
        self._reset_daily_state()
        return {
            "enabled": self.config.enabled,
            "event_hours": self.config.event_hours,
            "generated_hours": list(self._generated_hours),
            "today": self._get_today_str(),
        }
