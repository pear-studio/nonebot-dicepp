"""
角色生活模拟

管理角色的全天生活事件生成和日记记录。
事件触发时刻由角色卡 PersonaExtensions.generate_event_times() 决定（日内分钟槽位）。
"""
import asyncio
import json
import logging
import random
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Set

from ..agents.event_agent import EventGenerationAgent, EventContext, EventGenerationResult, EventReactionResult
from ..character.models import Character
from ..data.store import PersonaDataStore
from ..data.persist_keys import PERSONA_SK_CHARACTER_LIFE
from ..wall_clock import persona_wall_now
from .protocols import BoundaryReceiver

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
        min_event_interval_minutes: int = 5,
        chain_max_depth: int = 3,
        chain_force_extend_once_prob: float = 0.0,
        recovery_energy: int = 20,
        recovery_mood: int = 10,
        recovery_health: int = 5,
        default_energy: int = 50,
        default_mood: int = 50,
        default_health: int = 50,
    ):
        self.enabled = enabled
        # 当前「时:分」与计划槽位（自 0 点起的分钟）相差不超过该值则触发；tick 约 60s 一轮
        self.slot_match_window_minutes = slot_match_window_minutes
        self.diary_time = diary_time  # HH:MM format
        self.timezone = timezone
        self.min_event_interval_minutes = min_event_interval_minutes
        # 事件-反应链配置
        self.chain_max_depth = max(1, min(10, chain_max_depth))
        self.chain_force_extend_once_prob = chain_force_extend_once_prob
        # 跨天恢复数值
        self.recovery_energy = recovery_energy
        self.recovery_mood = recovery_mood
        self.recovery_health = recovery_health
        # 旧版纯文本状态迁移默认值
        self.default_energy = default_energy
        self.default_mood = default_mood
        self.default_health = default_health

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
        # 当日计划槽位（自 0 点起的分钟, 槽位类型），边界槽位类型为 wake_up/good_night，日常为 system
        self._slot_minutes_today: Optional[List[Tuple[int, str]]] = None
        self._fired_slot_indices: Set[int] = set()
        self._last_event_date: Optional[str] = None
        self._ongoing_activities: List[OngoingActivity] = []
        # 活跃时间波动边界（分钟，自 0 点起）
        self._today_jittered_start: Optional[int] = None
        self._today_jittered_end: Optional[int] = None
        # 今天是否已触发过链式事件（深度 >= 2）
        self._chain_triggered_today: bool = False
        # 上次执行跨天恢复的日期（防止无 good_night 时每日重复恢复）
        self._day_transition_recovered_date: Optional[str] = None
        # 可选的边界通知器，用于同步波动边界和标记边界事件
        self.boundary_notifier: Optional[BoundaryReceiver] = None

    def _get_today_str(self) -> str:
        return self.config.now().strftime("%Y-%m-%d")

    def _compute_daily_boundaries(self) -> tuple[int, int, random.Random]:
        """根据日期+角色名 seed 稳定采样今日波动边界，返回(起床分钟, 睡觉分钟, seeded_rng)。"""
        today_str = self._get_today_str()
        seed_str = f"{today_str}:{self.character.name}"
        rng = random.Random(seed_str)

        start_jitter = self.character.extensions.event_day_start_jitter_minutes
        end_jitter = self.character.extensions.event_day_end_jitter_minutes

        start_base = self.character.extensions.event_day_start_hour * 60
        end_base = self.character.extensions.event_day_end_hour * 60

        start_jitter_val = rng.randint(-start_jitter, start_jitter)
        end_jitter_val = rng.randint(-end_jitter, end_jitter)

        start_time = start_base + start_jitter_val
        end_time = end_base + end_jitter_val
        # 确保至少活跃 1 小时（避免 start == end 导致 scheduler 与槽位过滤不一致）
        # 仅在非跨午夜场景下修正（跨午夜时 start_time >= end_time 是正常的）
        if start_base < end_base and start_time >= end_time:
            end_time = start_time + 60
        return start_time, end_time, rng

    def _regenerate_slots_for_today(self) -> None:
        start, end, rng = self._compute_daily_boundaries()
        self._today_jittered_start = start
        self._today_jittered_end = end
        # 同步波动边界到 notifier，确保活跃时间判定一致
        if self.boundary_notifier is not None:
            self.boundary_notifier.set_jittered_boundaries(start, end)
        min_interval = self.config.min_event_interval_minutes
        # 前置约束：调整可用区间以避开边界区域
        constrained_start = start + min_interval
        constrained_end = end - min_interval
        slots: List[Tuple[int, str]] = []
        # 边界槽位
        slots.append((start, "wake_up"))
        slots.append((end, "good_night"))
        # 日常槽位
        if constrained_start < constrained_end:
            raw_slots = self.character.extensions.generate_event_times(
                start_minute=constrained_start, end_minute=constrained_end, rng=rng
            )
            for s in raw_slots:
                slots.append((s, "system"))
        else:
            logger.warning(
                "角色 %s 当日可用区间过短（%02d:%02d-%02d:%02d，min_interval=%d），仅生成边界槽位",
                self.character.name, start // 60, start % 60, end // 60, end % 60, min_interval
            )
        self._slot_minutes_today = sorted(slots, key=lambda x: x[0])
        logger.debug(
            "角色生活当日槽位 %s: %s (边界: %02d:%02d-%02d:%02d)",
            self._get_today_str(), self._slot_minutes_today,
            start // 60, start % 60, end // 60, end % 60,
        )

    def _reset_daily_state(self) -> None:
        """按日历日切换时重置槽位；同日则保证槽位已加载。"""
        today = self._get_today_str()
        if self._last_event_date == today:
            if self._slot_minutes_today is None:
                self._regenerate_slots_for_today()
            return
        self._fired_slot_indices.clear()
        self._chain_triggered_today = False
        self._today_jittered_start = None
        self._today_jittered_end = None
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
            # 兼容旧版：纯整数列表转为 system 类型槽位
            self._slot_minutes_today = []
            for item in sm:
                if item is None:
                    continue
                if isinstance(item, list) and len(item) == 2:
                    self._slot_minutes_today.append((int(item[0]), str(item[1])))
                else:
                    self._slot_minutes_today.append((int(item), "system"))
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
        # 加载链式触发标记
        self._chain_triggered_today = bool(data.get("chain_triggered"))
        # 加载上次跨天恢复日期
        self._day_transition_recovered_date = data.get("day_transition_recovered_date")

    async def save_persistent_state(self) -> None:
        today = self._get_today_str()
        if self._slot_minutes_today is None:
            self._regenerate_slots_for_today()
        payload = {
            "date": self._last_event_date or today,
            "slot_minutes": [[m, t] for m, t in (self._slot_minutes_today or [])],
            "fired": sorted(self._fired_slot_indices),
            "ongoing_activities": [
                {
                    "description": a.description,
                    "started_at": a.started_at.isoformat(),
                    "duration_minutes": a.duration_minutes,
                }
                for a in self._ongoing_activities
            ],
            "chain_triggered": self._chain_triggered_today,
            "day_transition_recovered_date": self._day_transition_recovered_date,
        }
        await self.data_store.set_setting(
            PERSONA_SK_CHARACTER_LIFE,
            json.dumps(payload, ensure_ascii=False),
        )

    async def tick(self) -> Optional[List[Dict[str, Any]]]:
        """
        检查是否需要生成事件（统一遍历所有槽位，包括边界事件和日常事件）

        Returns:
            生成的事件链列表，如果没有则返回 None
        """
        if not self.config.enabled:
            return None

        old_date = self._last_event_date
        self._reset_daily_state()
        self._cleanup_expired_activities()

        # 跨天处理：恢复兜底
        if old_date and old_date != self._last_event_date:
            try:
                await self._handle_day_transition(old_date)
                await self.save_persistent_state()
            except Exception:
                logger.exception("跨天恢复失败")

        now = self.config.now()
        now_m = now.hour * 60 + now.minute

        slots = self._slot_minutes_today
        if not slots:
            return None

        win = max(1, self.config.slot_match_window_minutes)

        for i, (slot_m, slot_type) in enumerate(slots):
            if i in self._fired_slot_indices:
                continue
            if abs(now_m - slot_m) > win:
                continue
            event_chain = await self._generate_daily_event(slot_type)
            if event_chain:
                self._fired_slot_indices.add(i)
                await self.save_persistent_state()
                return event_chain

        return None

    def _migrate_legacy_state(self, state: Any) -> None:
        """初始化旧版纯文本迁移的 None 状态（原地修改）"""
        if state.energy is None:
            state.energy = self.config.default_energy
        if state.mood is None:
            state.mood = self.config.default_mood
        if state.health is None:
            state.health = self.config.default_health

    async def _handle_day_transition(self, old_date: str) -> None:
        """跨天处理：检查昨晚是否有 good_night 事件，没有则兜底恢复（每 old_date 仅一次）"""
        # 直接查询数据库判断昨晚是否有 good_night 事件（事件保留 30 天）
        old_events = await self.data_store.get_daily_events(old_date)
        has_sleep_event = any(e.event_type == "good_night" for e in old_events)

        if not has_sleep_event and self._day_transition_recovered_date != old_date:
            state = await self.data_store.get_character_state()
            if state:
                self._migrate_legacy_state(state)
                state.energy = min(100, state.energy + self.config.recovery_energy)
                state.mood = min(100, state.mood + self.config.recovery_mood)
                state.health = min(100, state.health + self.config.recovery_health)
                await self.data_store.update_character_state(state)
                logger.info(
                    "跨天基础恢复兜底触发: energy+%d mood+%d health+%d",
                    self.config.recovery_energy,
                    self.config.recovery_mood,
                    self.config.recovery_health,
                )
            # 状态恢复完成后再写标记，避免「标记已写但状态未恢复」的竞态窗口
            self._day_transition_recovered_date = old_date
            await self.save_persistent_state()

    @staticmethod
    def _clamp_delta(d: Optional[int]) -> int:
        """将 delta 值约束到 [-20, 20] 范围内。"""
        if d is None:
            return 0
        return max(-20, min(20, d))

    async def _generate_daily_event(self, slot_type: str = "system") -> List[Dict[str, Any]]:
        """生成每日生活事件（事件-反应链），边界事件复用同一流程。

        Args:
            slot_type: 槽位类型 ("system" | "wake_up" | "good_night")

        Returns:
            事件链列表，每个元素是一个事件字典
        """
        try:
            today = self._get_today_str()
            now = self.config.now()

            # 获取角色状态，初始化旧版迁移的 None 字段，处理跨天空清意向
            character_state = await self.data_store.get_character_state()
            if character_state:
                self._migrate_legacy_state(character_state)
                if (character_state.intention_created_at
                        and character_state.intention_created_at.date() != now.date()):
                    character_state.current_intention = None
                    character_state.intention_created_at = None
                    await self.data_store.update_character_state(character_state)
                    logger.debug("跨天空清意向: %s", today)

            # 加载一次上下文（链内复用）
            recent_diaries = await self._get_recent_diaries(3)
            today_db_events = await self._get_today_events()

            # 构建带时间戳的今日事件列表供 prompt 使用
            chain_events: List[Dict[str, str]] = []
            for e in today_db_events:
                evt_time = e.created_at.strftime("%H:%M") if e.created_at else "??:??"
                chain_events.append({"description": e.description, "time": evt_time})

            # ── 事件-反应链循环 ──
            chain_depth = 0
            is_fallback = False
            event_chain: List[Dict[str, Any]] = []

            # 根据槽位类型注入场景提示
            if slot_type == "wake_up":
                base_scenario = f"{self.character.scenario}\n\n【当前场景：角色刚刚醒来】"
            elif slot_type == "good_night":
                base_scenario = f"{self.character.scenario}\n\n【当前场景：角色准备入睡】"
            else:
                base_scenario = self.character.scenario

            while True:
                if chain_depth >= self.config.chain_max_depth:
                    break

                # 每环统一获取时间基准，避免 LLM 调用延迟导致时间漂移
                now = self.config.now()
                time_str = now.strftime("%H:%M")

                # 构建当前状态上下文
                ongoing = self.get_ongoing_activities()
                ongoing_context = "\n".join(
                    f"- 进行中: {a.description}" for a in ongoing
                ) if ongoing else ""

                state_context = (
                    f"体力{character_state.energy}/心情{character_state.mood}/健康{character_state.health}"
                )
                if ongoing_context:
                    state_context += "\n" + ongoing_context

                context = EventContext(
                    character_name=self.character.name,
                    character_description=self.character.description,
                    world=self.character.extensions.world,
                    scenario=base_scenario if chain_depth == 0 else self.character.scenario,
                    recent_diaries=recent_diaries,
                    today_events=list(chain_events),
                    permanent_state=character_state.text + ("\n当前状态: " + state_context if state_context else ""),
                    current_time=now,
                    energy=character_state.energy,
                    mood=character_state.mood,
                    health=character_state.health,
                    current_intention=character_state.current_intention,
                    intention_created_at=character_state.intention_created_at,
                )

                # 生成事件
                event_result = await self.event_agent.generate_event_result(context)

                # 单事件 delta 硬约束
                ed = CharacterLife._clamp_delta(event_result.energy_delta)
                md = CharacterLife._clamp_delta(event_result.mood_delta)
                hd = CharacterLife._clamp_delta(event_result.health_delta)

                # 更新状态
                character_state.energy = max(0, min(100, character_state.energy + ed))
                character_state.mood = max(0, min(100, character_state.mood + md))
                character_state.health = max(0, min(100, character_state.health + hd))
                await self.data_store.update_character_state(character_state)

                # 生成反应
                reaction_result = await self.event_agent.generate_event_reaction(
                    event=event_result.description,
                    character_name=self.character.name,
                    character_description=self.character.description,
                    share_policy="optional",
                    today_events=list(chain_events),
                    energy=character_state.energy,
                    mood=character_state.mood,
                    health=character_state.health,
                    current_intention=character_state.current_intention,
                )

                # 意向生命周期处理
                pending_plan = reaction_result.pending_plan
                if pending_plan is None:
                    pass  # 保持当前意向不变
                elif pending_plan == "":
                    character_state.current_intention = None
                    character_state.intention_created_at = None
                    await self.data_store.update_character_state(character_state)
                else:
                    character_state.current_intention = pending_plan
                    character_state.intention_created_at = now
                    await self.data_store.update_character_state(character_state)

                # 保存事件到数据库
                await self.data_store.add_daily_event(
                    date=today,
                    event_type=slot_type if chain_depth == 0 else "system",
                    description=event_result.description,
                    reaction=reaction_result.reaction,
                    share_desire=reaction_result.share_desire,
                    duration_minutes=event_result.duration_minutes,
                    system_prompt_digest="",
                    raw_response="",
                )

                if event_result.duration_minutes > 0:
                    self._add_ongoing_activity(event_result.description, event_result.duration_minutes)

                # 记录当前环事件到链
                event_chain.append({
                    "event_id": f"evt_{now.strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:8]}",
                    "description": event_result.description,
                    "reaction": reaction_result.reaction,
                    "share_desire": reaction_result.share_desire,
                    "duration_minutes": event_result.duration_minutes,
                    "time": time_str,
                    "slot_type": slot_type if chain_depth == 0 else "system",
                })

                # debug trace
                logger.debug(
                    "[chain] %s @ %s depth=%d energy=%d(%+d) mood=%d(%+d) health=%d(%+d) "
                    "follow_up=%r pending_plan=%r fallback=%s",
                    self.character.name, time_str, chain_depth + 1,
                    character_state.energy, ed,
                    character_state.mood, md,
                    character_state.health, hd,
                    reaction_result.follow_up_action,
                    pending_plan,
                    is_fallback,
                )

                # 添加到 chain_events 供下一轮使用
                chain_events.append({"description": event_result.description, "time": time_str})

                chain_depth += 1

                # 检查是否续链（follow_up_action 为 None 或空字符串均不续链）
                if reaction_result.follow_up_action is not None and reaction_result.follow_up_action != "":
                    if chain_depth >= 2:
                        self._chain_triggered_today = True
                    continue

                # follow_up_action 为空：检查保底
                if chain_depth == 1 and not self._chain_triggered_today and not is_fallback:
                    if random.random() < self.config.chain_force_extend_once_prob:
                        is_fallback = True
                        logger.info("[chain] 触发保底续写: %s", self.character.name)
                        continue

                break

            if event_chain:
                logger.info(
                    "生成生活事件链: %s... (深度=%d, 保底=%s)",
                    event_chain[0]["description"][:50],
                    chain_depth,
                    is_fallback,
                )
            return event_chain

        except Exception as e:
            logger.exception("生成生活事件失败: %s", e)
            return []

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

            # 获取当前状态
            character_state = await self.data_store.get_character_state()

            # 转换为字典格式（带时间戳）
            events_dict = []
            for e in events:
                evt_time = e.created_at.strftime("%H:%M") if e.created_at else "??:??"
                events_dict.append({
                    "description": e.description,
                    "reaction": e.reaction,
                    "time": evt_time,
                })

            # 生成日记
            diary_content = await self.event_agent.generate_diary(
                events=events_dict,
                character_name=self.character.name,
                character_description=self.character.description,
                yesterday_diary=yesterday_diary,
                energy=character_state.energy if character_state else None,
                mood=character_state.mood if character_state else None,
                health=character_state.health if character_state else None,
                current_intention=character_state.current_intention if character_state else None,
            )

            # 保存日记
            await self.data_store.save_diary(today, diary_content)

            # 保留当天事件（供历史查询），只清理 30 天前的事件
            await self._prune_old_daily_events(30)

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

    async def _prune_old_daily_events(self, keep_days: int) -> None:
        """清理旧每日事件"""
        try:
            deleted = await self.data_store.prune_daily_events(keep_days)
            if deleted > 0:
                logger.info(f"清理了 {deleted} 条旧每日事件")
        except Exception as e:
            logger.warning(f"清理旧每日事件失败: {e}")

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
            "chain_triggered_today": self._chain_triggered_today,
            "chain_max_depth": self.config.chain_max_depth,
            "chain_force_extend_once_prob": self.config.chain_force_extend_once_prob,
        }
