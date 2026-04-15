"""
主动消息调度器

管理定时问候、想念触发、事件分享等主动消息
"""
from typing import List, Dict, Optional, Set, Tuple, Any
from datetime import datetime, timedelta
import asyncio
import json
import random
import logging

from ..data.store import PersonaDataStore
from ..data.persist_keys import PERSONA_SK_SCHEDULER
from ..data.models import RelationshipState
from ..character.models import Character
from ..game.decay import DecayCalculator
from ..wall_clock import persona_wall_now
from ..agents.event_agent import EventGenerationAgent

logger = logging.getLogger("persona.scheduler")


class ProactiveConfig:
    """主动消息配置"""

    def __init__(
        self,
        enabled: bool = True,
        min_interval_hours: int = 4,
        max_shares_per_event: int = 10,
        share_time_window_minutes: int = 15,
        miss_enabled: bool = True,
        miss_min_hours: int = 72,
        miss_min_score: float = 40.0,
        greeting_phrases: Optional[Dict[str, List[str]]] = None,
        timezone: str = "Asia/Shanghai",
        share_threshold: float = 0.5,
    ):
        self.enabled = enabled
        self.min_interval_hours = min_interval_hours
        self.max_shares_per_event = max_shares_per_event
        self.share_time_window_minutes = share_time_window_minutes
        self.miss_enabled = miss_enabled
        self.miss_min_hours = miss_min_hours
        self.miss_min_score = miss_min_score
        self.greeting_phrases: Dict[str, List[str]] = dict(greeting_phrases or {})
        self.timezone = timezone
        self.share_threshold = share_threshold


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
        shared_with: Optional[Set[str]] = None,
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
        event_agent: Optional[EventGenerationAgent] = None,
        bot=None,
        decay_calculator: Optional[DecayCalculator] = None,
    ):
        self.config = config
        self.data_store = data_store
        self.character = character
        self.event_agent = event_agent
        self.bot = bot
        self._decay_calculator = decay_calculator

        self._last_tick: Optional[datetime] = None
        self._tick_interval = timedelta(seconds=60)  # 60秒节流

        self._pending_shares: List[PendingShare] = []
        self._last_proactive_time: Dict[str, datetime] = {}  # user_id -> last_time

        self._scheduled_events_today: Set[str] = set()  # 今天已触发的定时事件类型
        self._last_event_date: Optional[str] = None

        # 在首次异步使用时再创建，避免绑定到错误的事件循环
        self._share_lock: Optional[asyncio.Lock] = None
        self._last_persisted_scheduler_blob: Optional[str] = None

    def _get_share_lock(self) -> asyncio.Lock:
        if self._share_lock is None:
            self._share_lock = asyncio.Lock()
        return self._share_lock

    def _now(self) -> datetime:
        return persona_wall_now(self.config.timezone)

    def _effective_for_proactive(self, rel: RelationshipState) -> RelationshipState:
        """与对话侧一致：阈值/概率按惰性时间衰减后的综合分（不写库）。"""
        if not self._decay_calculator:
            return rel
        initial = float(self.character.extensions.initial_relationship)
        return self._decay_calculator.effective_relationship(rel, initial)

    def _get_today_str(self) -> str:
        return self._now().strftime("%Y-%m-%d")

    async def load_persistent_state(self) -> None:
        raw = await self.data_store.get_setting(PERSONA_SK_SCHEDULER)
        if not raw:
            return
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return
        today = self._get_today_str()
        self._pending_shares = []
        for p in data.get("pending", []):
            try:
                self._pending_shares.append(
                    PendingShare(
                        event_id=p["event_id"],
                        event_description=p["event_description"],
                        created_at=datetime.fromisoformat(p["created_at"]),
                        shared_with=set(p.get("shared_with", [])),
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
        if data.get("date") == today:
            self._scheduled_events_today = set(data.get("scheduled", []))
            self._last_event_date = today
        else:
            self._scheduled_events_today.clear()
            self._last_event_date = today
        self._last_persisted_scheduler_blob = json.dumps(
            self._scheduler_payload_dict(), ensure_ascii=False, sort_keys=True
        )

    def _scheduler_payload_dict(self) -> Dict[str, Any]:
        return {
            "date": self._get_today_str(),
            "scheduled": sorted(self._scheduled_events_today),
            "pending": [
                {
                    "event_id": e.event_id,
                    "event_description": e.event_description,
                    "created_at": e.created_at.isoformat(),
                    "shared_with": sorted(e.shared_with),
                }
                for e in sorted(
                    self._pending_shares,
                    key=lambda x: (x.created_at.isoformat(), x.event_id),
                )
            ],
        }

    async def persist_state(self) -> None:
        payload = self._scheduler_payload_dict()
        blob = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        if blob == self._last_persisted_scheduler_blob:
            return
        await self.data_store.set_setting(PERSONA_SK_SCHEDULER, blob)
        self._last_persisted_scheduler_blob = blob

    def _reset_daily_state(self) -> None:
        """重置每日状态"""
        today = self._get_today_str()
        if self._last_event_date != today:
            self._scheduled_events_today.clear()
            self._last_event_date = today
            logger.debug(f"重置每日调度状态: {today}")

    def _is_character_active(self) -> bool:
        """检查当前是否在角色活跃时间"""
        now = self._now()
        hour = now.hour
        start = self.character.extensions.event_day_start_hour
        end = self.character.extensions.event_day_end_hour

        if start < end:
            return start <= hour < end
        else:
            # start == end 时视为全天活跃（hour >= start or hour < end 恒为 True）
            return hour >= start or hour < end

    def _can_send_to_user(self, user_id: str) -> bool:
        """检查是否可以向用户发送主动消息（最小间隔）"""
        last_time = self._last_proactive_time.get(user_id)
        if not last_time:
            return True

        min_interval = timedelta(hours=self.config.min_interval_hours)
        return self._now() - last_time >= min_interval

    async def tick(self) -> List[Dict]:
        """
        定时调用（60秒节流）

        Returns:
            待发送的消息列表
        """
        if not self.config.enabled:
            return []

        now = self._now()

        # 节流检查
        if self._last_tick and (now - self._last_tick) < self._tick_interval:
            return []

        self._last_tick = now
        self._reset_daily_state()

        # 非活跃时段不发送，但不清理 pending_shares
        if not self._is_character_active():
            await self.persist_state()
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
        finally:
            try:
                await self.persist_state()
            except Exception:
                logger.exception("调度器状态持久化失败")

        return messages

    async def _check_scheduled_events(self) -> List[Dict]:
        """检查并触发定时事件（从角色卡 scheduled_events 读取）"""
        messages = []
        now = self._now()
        current_time = now.strftime("%H:%M")

        from ..character.models import SharePolicy

        scheduled_events = self.character.extensions.scheduled_events or []
        for event_config in scheduled_events:
            event_type = event_config.type
            time_range = event_config.time_range
            raw_share = getattr(event_config, "share", SharePolicy.OPTIONAL)
            share_policy = raw_share if isinstance(raw_share, SharePolicy) else SharePolicy(raw_share)

            if event_type in self._scheduled_events_today:
                continue

            if not self._is_in_time_range(current_time, time_range):
                continue

            # 现场生成事件
            event_desc = await self._generate_scheduled_event_description(event_type)
            if not event_desc:
                # 生成失败：不标记为已触发，允许下次再试
                continue

            reaction_result = None
            if self.event_agent:
                reaction_result = await self.event_agent.generate_event_reaction(
                    event=event_desc,
                    character_name=self.character.name,
                    character_description=self.character.description,
                    share_policy=share_policy.value,
                )

            if reaction_result:
                reaction = reaction_result.reaction
                share_desire = reaction_result.share_desire
            else:
                reaction = ""
                if share_policy == SharePolicy.REQUIRED:
                    share_desire = 1.0
                elif share_policy == SharePolicy.NEVER:
                    share_desire = 0.0
                else:
                    share_desire = 0.5

            # 保存到数据库
            today = self._get_today_str()
            await self.data_store.add_daily_event(
                date=today,
                event_type=event_type,
                description=event_desc,
                reaction=reaction,
                share_desire=share_desire,
            )

            # 生成成功并保存后，标记为今天已触发
            self._scheduled_events_today.add(event_type)

            # 判断是否发送（发送失败不影响已触发状态）
            should_send = False
            if share_policy == SharePolicy.REQUIRED:
                should_send = True
            elif share_policy == SharePolicy.OPTIONAL and share_desire >= self.config.share_threshold:
                should_send = True
            # SharePolicy.NEVER 不发送

            if should_send:
                targets = await self._select_share_targets()
                for target in targets[: self.config.max_shares_per_event]:
                    if not self._can_send_to_user(target.user_id):
                        continue
                    if await self.data_store.is_user_muted(target.user_id):
                        continue

                    msg = await self._create_proactive_message(
                        target, event_desc, event_type
                    )
                    if msg:
                        messages.append(msg)
                        self._last_proactive_time[target.user_id] = now

            break  # 每次 tick 只触发一个定时事件

        return messages

    async def _generate_scheduled_event_description(self, event_type: str) -> str:
        """System Agent 根据 event_type 和当前上下文生成事件描述"""
        if not self.event_agent:
            # 兜底：使用预设短语 + 事件类型
            return f"我正在{event_type}。"

        today = self._get_today_str()
        today_events = await self.data_store.get_daily_events(today)

        now = self._now()
        from ..agents.event_agent import EventContext
        context = EventContext(
            character_name=self.character.name,
            character_description=self.character.description,
            world=self.character.extensions.world,
            scenario=self.character.scenario,
            recent_diaries=[],
            today_events=[{"description": e.description} for e in today_events],
            permanent_state=f"当前定时事件类型: {event_type}",
            current_time=now,
        )
        # 使用 generate_event_result 获取结构化事件数据
        try:
            result = await self.event_agent.generate_event_result(context)
            return result.description
        except Exception as e:
            if isinstance(e, (AttributeError, TypeError)):
                raise
            logger.error(f"定时事件生成失败: {e}")
            return f"我正在{event_type}。"

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
        now = self._now()
        min_idle = timedelta(hours=self.config.miss_min_hours)

        try:
            relationships = await self._get_active_relationships()

            for rel in relationships:
                eff = self._effective_for_proactive(rel)
                # 检查最小好感度（与对话展示一致）
                if eff.composite_score < self.config.miss_min_score:
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
                # Phase 3: 检查用户是否关闭了主动消息
                if await self.data_store.is_user_muted(user_id):
                    continue

                # 检查概率 P = 0.40 + 0.40 * (score/100)
                probability = 0.40 + 0.40 * (eff.composite_score / 100)
                if random.random() > probability:
                    continue

                # 获取今天的一个事件作为素材（不再依赖 _pending_shares）
                today = self._get_today_str()
                today_events = await self.data_store.get_daily_events(today)
                if not today_events:
                    continue
                event = random.choice(today_events)
                event_desc = event.description

                # 生成想念消息
                target = ShareTarget(
                    user_id=user_id,
                    group_id="",  # 想念消息只发私聊
                    priority=int(eff.composite_score),
                    score=eff.composite_score,
                )

                msg = await self._create_miss_you_message(target, event_desc)
                if msg:
                    messages.append(msg)
                    self._last_proactive_time[user_id] = now

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
            return await self.data_store.list_active_relationships(
                min_score=self.config.miss_min_score,
                active_within_days=30
            )
        except Exception as e:
            logger.error(f"获取活跃关系失败: {e}")
            return []

    async def _get_unshared_event(self) -> Optional[PendingShare]:
        """获取一个未分享的生活事件"""
        window = timedelta(minutes=self.config.share_time_window_minutes)
        now = self._now()
        for event in self._pending_shares:
            if now - event.created_at > window:
                continue
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
                eff = self._effective_for_proactive(rel)
                if eff.composite_score >= 60 and not rel.group_id:
                    targets.append(
                        ShareTarget(
                            user_id=rel.user_id,
                            priority=100 + int(eff.composite_score),
                            score=eff.composite_score,
                        )
                    )
                elif 40 <= eff.composite_score < 60 and not rel.group_id:
                    targets.append(
                        ShareTarget(
                            user_id=rel.user_id,
                            priority=50 + int(eff.composite_score),
                            score=eff.composite_score,
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

    async def share_event_to_targets(self, description: str, max_shares: int) -> List[Dict]:
        """
        将事件分享给符合条件的分享目标。

        封装目标选择、可发送检查、mute 检查、throttle 时间更新，
        供 Orchestrator / DelayedTaskQueue 调用。

        Returns:
            成功创建的消息列表
        """
        targets = await self._select_share_targets()
        now = self._now()
        messages = []
        for target in targets[:max_shares]:
            if not self._can_send_to_user(target.user_id):
                continue
            if await self.data_store.is_user_muted(target.user_id):
                continue
            msg = {
                "user_id": target.user_id,
                "group_id": target.group_id,
                "content": description,
                "type": "random_event",
            }
            self._last_proactive_time[target.user_id] = now
            messages.append(msg)
        return messages

    async def _create_proactive_message(
        self,
        target: ShareTarget,
        event_description: str,
        event_type: str,
    ) -> Optional[Dict]:
        """创建主动消息"""
        try:
            phrases = self.config.greeting_phrases.get(event_type) or ["你好~"]
            greeting = random.choice(phrases)

            # 根据事件内容自然引入话题
            content = f"{greeting} {event_description}"

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
        event_description: str,
    ) -> Optional[Dict]:
        """创建想念消息"""
        try:
            intros = [
                "好久不见，想你啦~",
                "最近忙什么呢？",
                "好久没聊天了~",
                "突然想到你了~",
            ]

            intro = random.choice(intros)
            content = f"{intro} {event_description}"

            return {
                "user_id": target.user_id,
                "group_id": "",  # 想念消息只发私聊
                "content": content,
                "type": "miss_you",
            }

        except Exception as e:
            logger.error(f"创建想念消息失败: {e}")
            return None

    async def add_event_to_share(self, event_description: str) -> str:
        """
        添加生活事件到分享队列

        Returns:
            事件ID
        """
        event_id = f"evt_{self._now().strftime('%Y%m%d%H%M%S')}_{random.randint(1000, 9999)}"

        pending = PendingShare(
            event_id=event_id,
            event_description=event_description,
            created_at=self._now(),
        )
        self._pending_shares.append(pending)

        # 清理过旧的事件（超过24小时）
        self._cleanup_old_events()

        logger.debug(f"添加事件到分享队列: {event_id}")
        try:
            await self.persist_state()
        except Exception:
            logger.exception("分享队列持久化失败")
        return event_id

    def _cleanup_old_events(self) -> None:
        """清理过旧的事件"""
        cutoff = self._now() - timedelta(hours=24)
        self._pending_shares = [
            e for e in self._pending_shares if e.created_at > cutoff
        ]

    def get_status(self) -> Dict:
        """获取调度器状态（用于调试）"""
        return {
            "enabled": self.config.enabled,
            "is_character_active": self._is_character_active(),
            "pending_shares": len(self._pending_shares),
            "scheduled_today": list(self._scheduled_events_today),
            "last_proactive_count": len(self._last_proactive_time),
        }
