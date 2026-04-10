"""
主动消息调度器

管理定时问候、想念触发、事件分享等主动消息
"""
from typing import List, Dict, Optional, Set, Tuple
from datetime import datetime, timedelta
import asyncio
import random
import logging

from ..data.store import PersonaDataStore
from ..data.models import RelationshipState
from ..character.models import Character

logger = logging.getLogger("persona.scheduler")


class ProactiveConfig:
    """主动消息配置"""

    def __init__(
        self,
        enabled: bool = True,
        quiet_hours: Tuple[int, int] = (23, 7),
        min_interval_hours: int = 4,
        max_shares_per_event: int = 10,
        share_time_window_minutes: int = 5,
        miss_enabled: bool = True,
        miss_min_hours: int = 72,
        miss_min_score: float = 40.0,
    ):
        self.enabled = enabled
        self.quiet_hours = quiet_hours  # (start, end)
        self.min_interval_hours = min_interval_hours
        self.max_shares_per_event = max_shares_per_event
        self.share_time_window_minutes = share_time_window_minutes
        self.miss_enabled = miss_enabled
        self.miss_min_hours = miss_min_hours
        self.miss_min_score = miss_min_score


class ShareTarget:
    """分享目标"""

    def __init__(
        self,
        user_id: str,
        group_id: str = "",
        is_group: bool = False,
        priority: int = 0,
        score: float = 0.0,
    ):
        self.user_id = user_id
        self.group_id = group_id
        self.is_group = is_group
        self.priority = priority
        self.score = score


class PendingShare:
    """待分享的事件"""

    def __init__(
        self,
        event_id: str,
        event_description: str,
        created_at: datetime,
        shared_with: Set[str] = None,
    ):
        self.event_id = event_id
        self.event_description = event_description
        self.created_at = created_at
        self.shared_with = shared_with or set()


class ProactiveScheduler:
    """主动消息调度器"""

    def __init__(
        self,
        config: ProactiveConfig,
        data_store: PersonaDataStore,
        character: Character,
        bot=None,
    ):
        self.config = config
        self.data_store = data_store
        self.character = character
        self.bot = bot

        self._last_tick: Optional[datetime] = None
        self._tick_interval = timedelta(seconds=60)  # 60秒节流

        self._pending_shares: List[PendingShare] = []
        self._last_proactive_time: Dict[str, datetime] = {}  # user_id -> last_time

        self._scheduled_events_today: Set[str] = set()  # 今天已触发的定时事件类型
        self._last_event_date: Optional[str] = None

        # 用于保护 shared_with 修改的锁
        self._share_lock = asyncio.Lock()

    def _get_today_str(self) -> str:
        return datetime.now().strftime("%Y-%m-%d")

    def _reset_daily_state(self) -> None:
        """重置每日状态"""
        today = self._get_today_str()
        if self._last_event_date != today:
            self._scheduled_events_today.clear()
            self._last_event_date = today
            logger.debug(f"重置每日调度状态: {today}")

    def _is_quiet_hours(self) -> bool:
        """检查是否在安静时段"""
        now = datetime.now()
        hour = now.hour
        start, end = self.config.quiet_hours

        if start < end:
            return start <= hour < end
        else:  # 跨天，如 23:00-07:00
            return hour >= start or hour < end

    def _can_send_to_user(self, user_id: str) -> bool:
        """检查是否可以向用户发送主动消息（最小间隔）"""
        last_time = self._last_proactive_time.get(user_id)
        if not last_time:
            return True

        min_interval = timedelta(hours=self.config.min_interval_hours)
        return datetime.now() - last_time >= min_interval

    async def tick(self) -> List[Dict]:
        """
        定时调用（60秒节流）

        Returns:
            待发送的消息列表
        """
        if not self.config.enabled:
            return []

        now = datetime.now()

        # 节流检查
        if self._last_tick and (now - self._last_tick) < self._tick_interval:
            return []

        self._last_tick = now
        self._reset_daily_state()

        # 安静时段不发送
        if self._is_quiet_hours():
            return []

        messages = []

        try:
            # 检查定时事件（问候/作息等）
            scheduled = await self._check_scheduled_events()
            if scheduled:
                messages.extend(scheduled)

            # 检查想念触发
            miss_you = await self._check_missed_users()
            if miss_you:
                messages.extend(miss_you)

        except Exception as e:
            logger.exception(f"调度器 tick 失败: {e}")

        return messages

    async def _check_scheduled_events(self) -> List[Dict]:
        """检查并触发定时事件"""
        messages = []
        now = datetime.now()
        current_time = now.strftime("%H:%M")

        # 定义定时事件
        scheduled_events = [
            ("wake_up", "07:00-08:00", "早上好！"),
            ("lunch", "11:30-13:00", "中午好~"),
            ("afternoon", "14:00-15:00", "下午好"),
            ("dinner", "17:30-19:00", "晚上好~"),
            ("good_night", "22:00-23:00", "晚安"),
        ]

        for event_type, time_range, _ in scheduled_events:
            if event_type in self._scheduled_events_today:
                continue

            if self._is_in_time_range(current_time, time_range):
                # 触发事件
                self._scheduled_events_today.add(event_type)

                # 获取一个未分享的生活事件作为内容
                event = await self._get_unshared_event()
                if event:
                    targets = await self._select_share_targets()
                    for target in targets[: self.config.max_shares_per_event]:
                        if not self._can_send_to_user(target.user_id):
                            continue

                        msg = await self._create_proactive_message(
                            target, event, event_type
                        )
                        if msg:
                            messages.append(msg)
                            self._last_proactive_time[target.user_id] = now
                            # 使用锁保护对 shared_with 的修改
                            async with self._share_lock:
                                event.shared_with.add(target.user_id)

                break  # 每次 tick 只触发一个定时事件

        return messages

    def _is_in_time_range(self, current_time: str, time_range: str) -> bool:
        """检查当前时间是否在时间范围内"""
        try:
            start, end = time_range.split("-")
            current = datetime.strptime(current_time, "%H:%M")
            start_dt = datetime.strptime(start, "%H:%M")
            end_dt = datetime.strptime(end, "%H:%M")
            return start_dt <= current <= end_dt
        except ValueError:
            return False

    async def _check_missed_users(self) -> List[Dict]:
        """检查并触发想念消息"""
        if not self.config.miss_enabled:
            return []

        messages = []
        now = datetime.now()
        min_idle = timedelta(hours=self.config.miss_min_hours)

        try:
            # 获取所有关系记录
            # 注意：这里假设 store 有方法获取所有关系，如果没有需要添加
            # 简化处理：只检查有活跃关系的用户
            relationships = await self._get_active_relationships()

            for rel in relationships:
                # 检查最小好感度
                if rel.composite_score < self.config.miss_min_score:
                    continue

                # 检查空闲时间
                if not rel.last_interaction_at:
                    continue

                idle_time = now - rel.last_interaction_at
                if idle_time < min_idle:
                    continue

                # 检查最小间隔
                user_id = rel.user_id
                if not self._can_send_to_user(user_id):
                    continue

                # 检查概率 P = 0.40 + 0.40 * (score/100)
                probability = 0.40 + 0.40 * (rel.composite_score / 100)
                if random.random() > probability:
                    continue

                # 获取未分享事件
                event = await self._get_unshared_event()
                if not event:
                    continue

                # 生成想念消息
                target = ShareTarget(
                    user_id=user_id,
                    group_id=rel.group_id,
                    priority=int(rel.composite_score),
                    score=rel.composite_score,
                )

                msg = await self._create_miss_you_message(target, event)
                if msg:
                    messages.append(msg)
                    self._last_proactive_time[user_id] = now
                    # 使用锁保护对 shared_with 的修改
                    async with self._share_lock:
                        event.shared_with.add(user_id)

                    # 限制每次 tick 只发送一条想念消息
                    break

        except Exception as e:
            logger.exception(f"检查想念触发失败: {e}")

        return messages

    async def _get_active_relationships(self) -> List[RelationshipState]:
        """获取活跃的关系记录（用于想念触发）

        Returns:
            最近30天内有互动且好感度 >= miss_min_score 的关系列表
        """
        try:
            return await self.data_store.get_all_relationships(
                min_score=self.config.miss_min_score,
                active_within_days=30
            )
        except Exception as e:
            logger.error(f"获取活跃关系失败: {e}")
            return []

    async def _get_unshared_event(self) -> Optional[PendingShare]:
        """获取一个未分享的生活事件"""
        # 从 pending_shares 中找到未分享的
        for event in self._pending_shares:
            if len(event.shared_with) < self.config.max_shares_per_event:
                return event
        return None

    async def _select_share_targets(self) -> List[ShareTarget]:
        """
        选择分享目标
        优先级：
        1. 私聊高好感度用户 (>=60)
        2. 私聊中等好感度用户 (40-60)
        3. 探索配额用户 (20-40, 7天内有互动)
        4. 群聊 (活跃度>=60)
        """
        targets = []

        try:
            # 获取高好感度用户
            high_score = await self.data_store.get_top_relationships(limit=20)
            for rel in high_score:
                if rel.composite_score >= 60 and not rel.group_id:
                    targets.append(
                        ShareTarget(
                            user_id=rel.user_id,
                            priority=100 + int(rel.composite_score),
                            score=rel.composite_score,
                        )
                    )
                elif 40 <= rel.composite_score < 60 and not rel.group_id:
                    targets.append(
                        ShareTarget(
                            user_id=rel.user_id,
                            priority=50 + int(rel.composite_score),
                            score=rel.composite_score,
                        )
                    )

            # 获取活跃群聊
            try:
                # 安全地获取配置值（self.bot 可能为 None）
                if self.bot and hasattr(self.bot, 'config') and hasattr(self.bot.config, 'persona_ai'):
                    min_activity = getattr(
                        self.bot.config.persona_ai, 'group_activity_min_threshold', 60
                    )
                else:
                    min_activity = 60  # 默认值
                group_activities = await self.data_store.get_all_group_activities(
                    min_score=min_activity
                )
                for activity in group_activities:
                    targets.append(
                        ShareTarget(
                            user_id="",
                            group_id=activity.group_id,
                            is_group=True,
                            priority=int(activity.score),
                            score=activity.score,
                        )
                    )
            except Exception as e:
                logger.debug(f"获取群活跃度失败: {e}")

            # 按优先级排序
            targets.sort(key=lambda x: x.priority, reverse=True)

        except Exception as e:
            logger.error(f"选择分享目标失败: {e}")

        return targets[: self.config.max_shares_per_event]

    async def _create_proactive_message(
        self,
        target: ShareTarget,
        event: PendingShare,
        event_type: str,
    ) -> Optional[Dict]:
        """创建主动消息"""
        try:
            # 基于事件类型和事件内容生成消息
            greetings = {
                "wake_up": ["早上好！", "早安~", "起床啦~"],
                "lunch": ["中午好~", "午饭吃了吗？", "午休时间~"],
                "afternoon": ["下午好", "下午过得怎么样？", "下午有空吗？"],
                "dinner": ["晚上好~", "吃晚饭了吗？", "晚上有空聊天吗？"],
                "good_night": ["晚安", "早点休息~", "好梦~"],
            }

            greeting = random.choice(greetings.get(event_type, ["你好~"]))

            # 根据事件内容自然引入话题
            content = f"{greeting} {event.event_description}"

            return {
                "user_id": target.user_id,
                "group_id": target.group_id,
                "content": content,
                "type": event_type,
            }

        except Exception as e:
            logger.error(f"创建主动消息失败: {e}")
            return None

    async def _create_miss_you_message(
        self,
        target: ShareTarget,
        event: PendingShare,
    ) -> Optional[Dict]:
        """创建想念消息"""
        try:
            # 基于未分享事件生成想念消息
            intros = [
                "好久不见，想你啦~",
                "最近忙什么呢？",
                "好久没聊天了~",
                "突然想到你了~",
            ]

            intro = random.choice(intros)
            content = f"{intro} {event.event_description}"

            return {
                "user_id": target.user_id,
                "group_id": "",  # 想念消息只发私聊
                "content": content,
                "type": "miss_you",
            }

        except Exception as e:
            logger.error(f"创建想念消息失败: {e}")
            return None

    def add_event_to_share(self, event_description: str) -> str:
        """
        添加生活事件到分享队列

        Returns:
            事件ID
        """
        event_id = f"evt_{datetime.now().strftime('%Y%m%d%H%M%S')}_{random.randint(1000, 9999)}"

        pending = PendingShare(
            event_id=event_id,
            event_description=event_description,
            created_at=datetime.now(),
        )
        self._pending_shares.append(pending)

        # 清理过旧的事件（超过24小时）
        self._cleanup_old_events()

        logger.debug(f"添加事件到分享队列: {event_id}")
        return event_id

    def _cleanup_old_events(self) -> None:
        """清理过旧的事件"""
        cutoff = datetime.now() - timedelta(hours=24)
        self._pending_shares = [
            e for e in self._pending_shares if e.created_at > cutoff
        ]

    def get_status(self) -> Dict:
        """获取调度器状态（用于调试）"""
        return {
            "enabled": self.config.enabled,
            "quiet_hours": self.config.quiet_hours,
            "is_quiet_hours": self._is_quiet_hours(),
            "pending_shares": len(self._pending_shares),
            "scheduled_today": list(self._scheduled_events_today),
            "last_proactive_count": len(self._last_proactive_time),
        }
