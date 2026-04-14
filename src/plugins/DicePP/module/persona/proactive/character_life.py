"""
角色生活模拟

管理角色的全天生活事件生成和日记记录。
事件触发时刻由角色卡 PersonaExtensions.generate_event_times() 决定（日内分钟槽位）。
"""
from typing import List, Optional, Dict, Any, Set
from datetime import datetime, timedelta
import json
import logging

from ..agents.event_agent import EventGenerationAgent, EventContext
from ..character.models import Character
from ..data.store import PersonaDataStore
from ..data.persist_keys import PERSONA_SK_CHARACTER_LIFE
from ..wall_clock import persona_wall_now

logger = logging.getLogger("persona.character_life")


class CharacterLifeConfig:
    """角色生活模拟配置（时刻分布来自角色卡，此处仅运行参数）"""

    def __init__(
        self,
        enabled: bool = True,
        slot_match_window_minutes: int = 15,
        diary_time: str = "23:30",
        timezone: str = "Asia/Shanghai",
    ):
        self.enabled = enabled
        # 当前「时:分」与计划槽位（自 0 点起的分钟）相差不超过该值则触发；tick 约 60s 一轮
        self.slot_match_window_minutes = slot_match_window_minutes
        self.diary_time = diary_time  # HH:MM format
        self.timezone = timezone

    def now(self) -> datetime:
        return persona_wall_now(self.timezone)


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
        # 当日计划槽位（自 0 点起的分钟，与 generate_event_times 一致）
        self._slot_minutes_today: Optional[List[int]] = None
        self._fired_slot_indices: Set[int] = set()
        self._last_event_date: Optional[str] = None

    def _get_today_str(self) -> str:
        return self.config.now().strftime("%Y-%m-%d")

    def _regenerate_slots_for_today(self) -> None:
        self._slot_minutes_today = sorted(self.character.extensions.generate_event_times())
        logger.debug("角色生活当日槽位 %s: %s", self._get_today_str(), self._slot_minutes_today)

    def _reset_daily_state(self) -> None:
        """按日历日切换时重置槽位；同日则保证槽位已加载。"""
        today = self._get_today_str()
        if self._last_event_date == today:
            if self._slot_minutes_today is None:
                self._regenerate_slots_for_today()
            return
        self._fired_slot_indices.clear()
        self._regenerate_slots_for_today()
        self._last_event_date = today
        logger.debug("重置每日事件状态: %s", today)

    async def load_persistent_state(self) -> None:
        raw = await self.data_store.get_setting(PERSONA_SK_CHARACTER_LIFE)
        if not raw:
            return
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return
        today = self._get_today_str()
        if data.get("date") != today:
            return
        self._last_event_date = today
        sm = data.get("slot_minutes")
        if isinstance(sm, list) and sm:
            self._slot_minutes_today = [int(x) for x in sm if x is not None]
        else:
            # 旧版仅持久化 hours，无法还原分钟槽位：当日重新采样
            self._regenerate_slots_for_today()
        fired = data.get("fired")
        if isinstance(fired, list):
            self._fired_slot_indices = {int(x) for x in fired if x is not None}
        else:
            self._fired_slot_indices = set()

    async def save_persistent_state(self) -> None:
        today = self._get_today_str()
        if self._slot_minutes_today is None:
            self._regenerate_slots_for_today()
        payload = {
            "date": self._last_event_date or today,
            "slot_minutes": list(self._slot_minutes_today or []),
            "fired": sorted(self._fired_slot_indices),
        }
        await self.data_store.set_setting(
            PERSONA_SK_CHARACTER_LIFE,
            json.dumps(payload, ensure_ascii=False),
        )

    async def tick(self) -> Optional[Dict[str, str]]:
        """
        检查是否需要生成事件

        Returns:
            生成的事件和反应，如果没有则返回 None
        """
        if not self.config.enabled:
            return None

        old_date = self._last_event_date
        self._reset_daily_state()
        if old_date != self._last_event_date:  # 移除 None 检查，确保首次运行也保存
            await self.save_persistent_state()

        slots = self._slot_minutes_today
        if not slots:
            return None

        now = self.config.now()
        now_m = now.hour * 60 + now.minute
        win = max(1, self.config.slot_match_window_minutes)

        for i, slot_m in enumerate(slots):
            if i in self._fired_slot_indices:
                continue
            if abs(now_m - slot_m) > win:
                continue
            event_data = await self._generate_daily_event()
            if event_data:
                self._fired_slot_indices.add(i)
                await self.save_persistent_state()
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
                scenario=self.character.scenario,
                recent_diaries=recent_diaries,
                today_events=[{"description": e.description} for e in today_events],
                permanent_state=permanent_state,
                current_time=self.config.now(),
            )

            # 生成事件
            event_desc = await self.event_agent.generate_event(context)

            # 生成反应
            reaction = await self.event_agent.generate_reaction(
                event=event_desc,
                character_name=self.character.name,
                character_description=self.character.description,
                today_events=[{"description": e.description} for e in today_events],
            )

            # 保存到数据库
            await self.data_store.add_daily_event(
                date=today,
                event_type="scheduled",
                description=event_desc,
                reaction=reaction,
                system_prompt_digest="",
                raw_response="",
            )

            logger.info(f"生成生活事件: {event_desc[:50]}...")

            return {
                "description": event_desc,
                "reaction": reaction,
                "time": self.config.now().strftime("%H:%M"),
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
            yesterday = (self.config.now() - timedelta(days=1)).strftime("%Y-%m-%d")
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
            date = (self.config.now() - timedelta(days=i)).strftime("%Y-%m-%d")
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
            "slot_minutes": list(self._slot_minutes_today or []),
            "fired_slot_indices": sorted(self._fired_slot_indices),
            "today": self._get_today_str(),
            "daily_events_count": self.character.extensions.daily_events_count,
            "event_day_start_hour": self.character.extensions.event_day_start_hour,
            "event_day_end_hour": self.character.extensions.event_day_end_hour,
            "event_jitter_minutes": self.character.extensions.event_jitter_minutes,
        }
