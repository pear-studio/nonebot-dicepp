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
import re

from ..data.store import PersonaDataStore
from ..data.persist_keys import PERSONA_SK_SCHEDULER
from ..data.models import RelationshipState
from ..character.models import Character
from ..game.decay import DecayCalculator
from ..wall_clock import persona_wall_now
from ..agents.event_agent import EventGenerationAgent, ShareMessageContext
from .models import ShareTarget
from .utils import effective_for_proactive

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
        timezone: str = "Asia/Shanghai",
        share_threshold: float = 0.5,
        share_message_concurrent: int = 3,
        share_max_chars: int = 200,
        share_context_history_limit: int = 5,
        max_scheduled_events_per_tick: int = 3,
    ):
        self.enabled = enabled
        self.min_interval_hours = min_interval_hours
        self.max_shares_per_event = max_shares_per_event
        self.share_time_window_minutes = share_time_window_minutes
        self.miss_enabled = miss_enabled
        self.miss_min_hours = miss_min_hours
        self.miss_min_score = miss_min_score
        self.timezone = timezone
        self.share_threshold = share_threshold
        self.share_message_concurrent = share_message_concurrent
        self.share_max_chars = share_max_chars
        self.share_context_history_limit = share_context_history_limit
        self.max_scheduled_events_per_tick = max_scheduled_events_per_tick


class ProactiveScheduler:
    """主动消息调度器"""

    def __init__(
        self,
        config: ProactiveConfig,
        data_store: PersonaDataStore,
        character: Character,
        target_selector: "TargetSelector",
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
        self.target_selector = target_selector

        self._last_tick: Optional[datetime] = None
        self._tick_interval = timedelta(seconds=60)  # 60秒节流

        self._last_proactive_time: Dict[str, datetime] = {}  # target_key -> last_time

        self._scheduled_events_today: Set[str] = set()  # 今天已触发的定时事件类型
        self._last_event_date: Optional[str] = None
        self._pending_targets: Set[str] = set()  # 当前正在处理的目标（防并发重复）

        # 在首次异步使用时再创建，避免绑定到错误的事件循环
        self._share_lock: Optional[asyncio.Lock] = None
        self._last_persisted_scheduler_blob: Optional[str] = None

    def _get_share_lock(self) -> asyncio.Lock:
        if self._share_lock is None:
            self._share_lock = asyncio.Lock()
        return self._share_lock

    def _now(self) -> datetime:
        return persona_wall_now(self.config.timezone)

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
        if data.get("date") == today:
            self._scheduled_events_today = set(data.get("scheduled", []))
            self._last_event_date = today
        else:
            self._scheduled_events_today.clear()
            self._last_event_date = today
        old_pending = data.get("pending")
        if old_pending:
            logger.warning(
                f"检测到旧版本 pending 事件数据，共 {len(old_pending)} 条，已被丢弃。"
                f"建议检查是否有未分享的事件。"
            )
        self._last_persisted_scheduler_blob = json.dumps(
            self._scheduler_payload_dict(), ensure_ascii=False, sort_keys=True
        )

    def _scheduler_payload_dict(self) -> Dict[str, Any]:
        return {
            "date": self._get_today_str(),
            "scheduled": sorted(self._scheduled_events_today),
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
        elif start > end:
            # 跨午夜时段，如 start=22, end=6
            return hour >= start or hour < end
        else:
            # start == end，视为全天活跃
            return True

    def _target_key(self, target: ShareTarget) -> str:
        return f"group:{target.group_id}" if target.is_group else f"user:{target.user_id}"

    def _can_send_to_key(self, key: str) -> bool:
        """检查是否可以对指定目标发送主动消息（最小间隔）"""
        last_time = self._last_proactive_time.get(key)
        if not last_time:
            return True

        min_interval = timedelta(hours=self.config.min_interval_hours)
        return self._now() - last_time >= min_interval

    async def _can_send_to_target(self, target: ShareTarget) -> bool:
        key = self._target_key(target)
        if key in self._pending_targets:
            logger.debug(
                f"主动消息跳过(处理中): user={target.user_id}, group={target.group_id}"
            )
            return False
        if target.policy != "force" and not self._can_send_to_key(key):
            logger.debug(
                f"主动消息跳过(间隔): user={target.user_id}, group={target.group_id}"
            )
            return False
        if not target.is_group and await self.data_store.is_user_muted(target.user_id):
            logger.debug(
                f"主动消息跳过(静音): user={target.user_id}, group={target.group_id}"
            )
            return False
        return True

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
        processed = 0
        max_per_tick = self.config.max_scheduled_events_per_tick

        from ..character.models import SharePolicy

        scheduled_events = self.character.extensions.scheduled_events or []
        for event_config in scheduled_events:
            event_type = event_config.type
            time_range = event_config.time_range
            raw_share = getattr(event_config, "share", SharePolicy.OPTIONAL)
            share_policy = raw_share if isinstance(raw_share, SharePolicy) else SharePolicy(raw_share)

            if event_type in self._scheduled_events_today:
                logger.debug(f"定时事件已触发过: {event_type}")
                continue

            if not self._is_in_time_range(current_time, time_range):
                continue

            logger.info(
                f"定时事件触发: type={event_type}, time={current_time}, "
                f"share_policy={share_policy.value}"
            )

            # 现场生成事件
            event_desc = await self._generate_scheduled_event_description(event_type)
            if not event_desc:
                logger.warning(f"定时事件生成失败, 下次重试: {event_type}")
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

            logger.debug(
                f"定时事件生成完成: type={event_type}, desc={event_desc[:40]}, "
                f"share_desire={share_desire:.2f}"
            )

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
                targets = await self.target_selector.select_share_targets()
                sent_count = 0
                for target in targets[: self.config.max_shares_per_event]:
                    if not await self._can_send_to_target(target):
                        continue

                    msg = await self._create_proactive_message(
                        target, event_desc, event_type, reaction
                    )
                    if msg:
                        messages.append(msg)
                        self._last_proactive_time[self._target_key(target)] = now
                        sent_count += 1
                        logger.info(
                            f"主动消息发送: target={target.user_id}, "
                            f"group={target.group_id}, is_group={target.is_group}, "
                            f"event_type={event_type}, score={target.score:.1f}"
                        )
                if not sent_count:
                    logger.debug(f"定时事件无可发送目标: {event_type}")
            else:
                logger.debug(
                    f"定时事件跳过发送: {event_type}, policy={share_policy.value}, "
                    f"desire={share_desire:.2f}, threshold={self.config.share_threshold:.2f}"
                )

            processed += 1
            if processed >= max_per_tick:
                break  # 本次 tick 已达到上限，剩余事件留到下次

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
        except (AttributeError, TypeError):
            raise
        except Exception as e:
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
            logger.debug(f"想念检查: 活跃关系数={len(relationships)}")

            for rel in relationships:
                eff = effective_for_proactive(rel, self._decay_calculator, self.character)
                # 检查最小好感度（与对话展示一致）
                if eff.composite_score < self.config.miss_min_score:
                    logger.debug(
                        f"想念跳过(好感度低): user={rel.user_id}, "
                        f"score={eff.composite_score:.1f}"
                    )
                    continue

                # 检查空闲时间
                if not rel.last_interaction_at:
                    continue

                idle_time = now - rel.last_interaction_at
                idle_hours = idle_time.total_seconds() / 3600
                if idle_time < min_idle:
                    logger.debug(
                        f"想念跳过(空闲短): user={rel.user_id}, idle={idle_hours:.1f}h"
                    )
                    continue

                # 检查最小间隔
                user_id = rel.user_id
                if not self._can_send_to_key(f"user:{user_id}"):
                    logger.debug(f"想念跳过(间隔): user={user_id}")
                    continue
                # Phase 3: 检查用户是否关闭了主动消息
                if await self.data_store.is_user_muted(user_id):
                    logger.debug(f"想念跳过(静音): user={user_id}")
                    continue

                # 检查概率 P = 0.40 + 0.40 * (score/100)
                probability = 0.40 + 0.40 * (eff.composite_score / 100)
                if random.random() > probability:
                    logger.debug(
                        f"想念跳过(概率): user={user_id}, p={probability:.2f}"
                    )
                    continue

                # 获取今天的一个事件作为素材（不再依赖 _pending_shares）
                today = self._get_today_str()
                today_events = await self.data_store.get_daily_events(today)
                if not today_events:
                    logger.debug(f"想念跳过(无事件): user={user_id}")
                    continue
                event = random.choice(today_events)
                event_desc = event.description
                event_reaction = getattr(event, "reaction", "")

                # 生成想念消息
                target = ShareTarget(
                    user_id=user_id,
                    group_id="",  # 想念消息只发私聊
                    priority=int(eff.composite_score),
                    score=eff.composite_score,
                    policy="normal",
                )

                msg = await self._create_miss_you_message(target, event_desc, event_reaction)
                if msg:
                    messages.append(msg)
                    self._last_proactive_time[f"user:{user_id}"] = now
                    logger.info(
                        f"想念触发: user={user_id}, idle={idle_hours:.1f}h, "
                        f"score={eff.composite_score:.1f}, event={event_desc[:40]}"
                    )

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

    # ── 上下文格式化辅助方法 ──────────────────────────────

    @staticmethod
    def _sanitize_prompt_text(text: str, max_len: int = 800) -> str:
        """清理用户可控文本，防止破坏 prompt 结构。"""
        text = text.replace('"""', '"')
        text = re.sub(r'\n{3,}', '\n\n', text)
        if len(text) > max_len:
            text = text[:max_len - 3] + "..."
        return text

    @staticmethod
    def _format_user_profile_facts(profile) -> str:
        """将 UserProfile.facts 格式化为文本列表。"""
        if not profile or not profile.facts:
            return "（无）"
        lines = []
        for key, value in profile.facts.items():
            if isinstance(value, list):
                val_str = "、".join(str(v) for v in value)
            elif isinstance(value, dict):
                val_str = json.dumps(value, ensure_ascii=False, separators=(',', ':'))
            else:
                val_str = str(value)
            lines.append(f"- {key}：{val_str}")
        text = "\n".join(lines) if lines else "（无）"
        return ProactiveScheduler._sanitize_prompt_text(text)

    @staticmethod
    def _format_recent_history(messages, limit: int = 5) -> str:
        """将 Message 列表格式化为精简对话摘要。"""
        if not messages:
            return "（无）"
        lines = []
        role_map = {"user": "用户", "assistant": "我", "system": "系统", "tool": "工具"}
        for msg in messages[-limit:]:
            role_label = role_map.get(msg.role, "用户")
            content = msg.content
            if len(content) > 50:
                content = content[:47] + "..."
            lines.append(f"- {role_label}: {content}")
        text = "\n".join(lines)
        return ProactiveScheduler._sanitize_prompt_text(text)

    async def _build_and_generate_share_message(
        self,
        target: ShareTarget,
        event_description: str,
        reaction: str,
        message_type: str,
        environment: str,
    ) -> Optional[Dict]:
        """为单个目标构建并生成个性化分享消息。

        Returns:
            消息 dict，生成失败返回 None
        """
        if not self.event_agent:
            return None

        try:
            # 获取目标上下文
            user_profile = await self.data_store.get_user_profile(target.user_id)
            rel = await self.data_store.get_relationship(target.user_id, target.group_id)

            warmth_label = ""
            relationship_score = 0.0
            if rel:
                relationship_score = rel.composite_score
                labels = self.character.get_warmth_labels()
                _, warmth_label = rel.get_warmth_level(labels)

            recent_msgs = await self.data_store.get_recent_messages(
                target.user_id, target.group_id, limit=self.config.share_context_history_limit
            )

            share_examples = self.character.extensions.share_message_examples

            context = ShareMessageContext(
                event_description=event_description,
                reaction=reaction,
                character_name=self.character.name,
                character_description=self.character.description,
                target_user_id=target.user_id,
                relationship_score=relationship_score,
                warmth_label=warmth_label,
                user_profile_facts=self._format_user_profile_facts(user_profile),
                recent_history=self._format_recent_history(recent_msgs, self.config.share_context_history_limit),
                message_type=message_type,
                environment=environment,
                share_message_examples=share_examples,
            )

            message = await self.event_agent.generate_share_message(context)
            if not message:
                return None
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(
                f"构建分享上下文失败: user={target.user_id}, group={target.group_id}, error={e}"
            )
            return None

        return {
            "user_id": target.user_id,
            "group_id": target.group_id,
            "content": message,
            "type": message_type,
        }

    async def share_event_to_targets(
        self, description: str, reaction: str, max_shares: int
    ) -> List[Dict]:
        """
        将事件分享给符合条件的分享目标。

        封装目标选择、可发送检查、mute 检查、throttle 时间更新，
        供 Orchestrator / EventShareTaskQueue 调用。

        Returns:
            成功创建的消息列表
        """
        if not self.config.enabled:
            return []

        targets = await self.target_selector.select_share_targets()
        now = self._now()

        # 先全量过滤可发送目标，按 force 优先排序，再切片
        valid_targets = []
        for target in targets:
            if await self._can_send_to_target(target):
                valid_targets.append(target)
        valid_targets.sort(key=lambda t: 0 if t.policy == "force" else 1)
        actual_max = max_shares
        valid_targets = valid_targets[:actual_max]

        if not valid_targets:
            return []

        logger.debug(f"本次事件将触发 {len(valid_targets)} 次 LLM 调用生成分享消息")

        # 并发生成分享消息，限制并发数
        semaphore = asyncio.Semaphore(self.config.share_message_concurrent)

        async def _gen_for_target(target: ShareTarget) -> Optional[Dict]:
            key = self._target_key(target)
            self._pending_targets.add(key)
            try:
                async with semaphore:
                    msg_dict = await self._build_and_generate_share_message(
                        target=target,
                        event_description=description,
                        reaction=reaction,
                        message_type="random_event",
                        environment="group" if target.is_group else "private",
                    )
                    if msg_dict:
                        self._last_proactive_time[key] = now
                    return msg_dict
            finally:
                self._pending_targets.discard(key)

        results = await asyncio.gather(
            *[_gen_for_target(t) for t in valid_targets],
        )

        messages: List[Dict] = []
        for r in results:
            if r is not None:
                messages.append(r)

        return messages

    async def _create_proactive_message(
        self,
        target: ShareTarget,
        event_description: str,
        event_type: str,
        reaction: str,
    ) -> Optional[Dict]:
        """创建主动消息（定时事件）"""
        try:
            msg_dict = await self._build_and_generate_share_message(
                target=target,
                event_description=event_description,
                reaction=reaction,
                message_type=event_type,
                environment="group" if target.is_group else "private",
            )
            return msg_dict
        except Exception as e:
            logger.error(f"创建主动消息失败: {e}")
            return None

    async def _create_miss_you_message(
        self,
        target: ShareTarget,
        event_description: str,
        reaction: str,
    ) -> Optional[Dict]:
        """创建想念消息"""
        try:
            msg_dict = await self._build_and_generate_share_message(
                target=target,
                event_description=event_description,
                reaction=reaction,
                message_type="miss_you",
                environment="private",
            )
            return msg_dict
        except Exception as e:
            logger.error(f"创建想念消息失败: {e}")
            return None

    def get_status(self) -> Dict:
        """获取调度器状态（用于调试）"""
        return {
            "enabled": self.config.enabled,
            "is_character_active": self._is_character_active(),
            "scheduled_today": list(self._scheduled_events_today),
            "last_proactive_count": len(self._last_proactive_time),
        }
