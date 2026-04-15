"""
角色生活模拟

管理角色的全天生活事件生成和日记记录。
事件触发时刻由角色卡 PersonaExtensions.generate_event_times() 决定（日内分钟槽位）。
"""
from typing import List, Optional, Dict, Any, Set
from datetime import datetime, timedelta
import json
import logging
import uuid
from dataclasses import dataclass, asdict

from ..agents.event_agent import EventGenerationAgent, EventContext, EventGenerationResult, EventReactionResult
from ..character.models import Character
from ..data.store import PersonaDataStore
from ..data.persist_keys import PERSONA_SK_CHARACTER_LIFE
from ..wall_clock import persona_wall_now

logger = logging.getLogger("persona.character_life")


@dataclass
class OngoingActivity:
    description: str
    started_at: datetime
    duration_minutes: int

    def is_expired(self, now: datetime) -> bool:
        return now >= self.started_at + timedelta(minutes=self.duration_minutes)


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
        self._ongoing_activities: List[OngoingActivity] = []

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

    def _cleanup_expired_activities(self) -> None:
        now = self.config.now()
        before = len(self._ongoing_activities)
        self._ongoing_activities = [a for a in self._ongoing_activities if not a.is_expired(now)]
        if before != len(self._ongoing_activities):
            logger.debug(f"清理过期活动: {before - len(self._ongoing_activities)} 个")

    def get_ongoing_activities(self) -> List[OngoingActivity]:
        self._cleanup_expired_activities()
        return list(self._ongoing_activities)

    def _add_ongoing_activity(self, description: str, duration_minutes: int) -> None:
        if duration_minutes > 0:
            self._ongoing_activities.append(
                OngoingActivity(
                    description=description,
                    started_at=self.config.now(),
                    duration_minutes=duration_minutes,
                )
            )

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
        self._ongoing_activities = []
        activities = data.get("ongoing_activities")
        if isinstance(activities, list):
            for a in activities:
                try:
                    self._ongoing_activities.append(
                        OngoingActivity(
                            description=a["description"],
                            started_at=datetime.fromisoformat(a["started_at"]),
                            duration_minutes=int(a["duration_minutes"]),
                        )
                    )
                except (KeyError, TypeError, ValueError):
                    continue

    async def save_persistent_state(self) -> None:
        today = self._get_today_str()
        if self._slot_minutes_today is None:
            self._regenerate_slots_for_today()
        payload = {
            "date": self._last_event_date or today,
            "slot_minutes": list(self._slot_minutes_today or []),
            "fired": sorted(self._fired_slot_indices),
            "ongoing_activities": [
                {
                    "description": a.description,
                    "started_at": a.started_at.isoformat(),
                    "duration_minutes": a.duration_minutes,
                }
                for a in self._ongoing_activities
            ],
        }
        await self.data_store.set_setting(
            PERSONA_SK_CHARACTER_LIFE,
            json.dumps(payload, ensure_ascii=False),
        )

    async def tick(self) -> Optional[Dict[str, Any]]:
        """
        检查是否需要生成事件

        Returns:
            生成的事件和反应，如果没有则返回 None
        """
        if not self.config.enabled:
            return None

        old_date = self._last_event_date
        self._reset_daily_state()
        self._cleanup_expired_activities()
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

    async def _generate_daily_event(self) -> Optional[Dict[str, Any]]:
        """生成每日生活事件（随机事件槽位）"""
        try:
            today = self._get_today_str()

            recent_diaries = await self._get_recent_diaries(3)
            today_events = await self._get_today_events()
            permanent_state = await self.data_store.get_character_state()

            ongoing = self.get_ongoing_activities()
            ongoing_context = "\n".join(
                f"- 进行中: {a.description}" for a in ongoing
            ) if ongoing else ""

            context = EventContext(
                character_name=self.character.name,
                character_description=self.character.description,
                world=self.character.extensions.world,
                scenario=self.character.scenario,
                recent_diaries=recent_diaries,
                today_events=[{"description": e.description} for e in today_events],
                permanent_state=permanent_state + ("\n" + ongoing_context if ongoing_context else ""),
                current_time=self.config.now(),
            )

            event_result = await self.event_agent.generate_event_result(context)

            reaction_result = await self.event_agent.generate_event_reaction(
                event=event_result.description,
                character_name=self.character.name,
                character_description=self.character.description,
                share_policy="optional",
                today_events=[{"description": e.description} for e in today_events],
            )

            await self.data_store.add_daily_event(
                date=today,
                event_type="system",
                description=event_result.description,
                reaction=reaction_result.reaction,
                share_desire=reaction_result.share_desire,
                duration_minutes=event_result.duration_minutes,
                system_prompt_digest="",
                raw_response="",
            )

            if event_result.duration_minutes > 0:
                self._add_ongoing_activity(event_result.description, event_result.duration_minutes)

            logger.info(f"生成生活事件: {event_result.description[:50]}...")

            # event_id 是运行时生成的队列标识，仅用于 DelayedTaskQueue 去重/追踪，不与数据库 DailyEvent.id 关联
            return {
                "event_id": f"evt_{self.config.now().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:8]}",
                "description": event_result.description,
                "reaction": reaction_result.reaction,
                "share_desire": reaction_result.share_desire,
                "duration_minutes": event_result.duration_minutes,
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
