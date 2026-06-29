"""
角色生活模拟

管理角色的全天生活事件生成和日记记录。
Phase 1: 退回纯状态管理，LLM 调用委托给 DM/Character Agent。
"""
import asyncio
import json
import time
from utils.logger import logger
import random
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple, TYPE_CHECKING

from ..character.models import Character
from ..data.store import PersonaDataStore
from ..data.persist_keys import PERSONA_SK_CHARACTER_LIFE
from ..data.models import CharacterState, DMState
from utils.time import wall_now, format_timestamp
from .types import EventGenerationResult, EventReactionResult
from .protocols import BoundaryReceiver

if TYPE_CHECKING:
    from core.config.pydantic_models import PersonaConfig
    from .dm_agent import DMAgent
    from .character_agent import CharacterAgent


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
        timezone: str = "Asia/Shanghai",
        min_event_interval_minutes: int = 5,
        chain_max_depth: int = 3,
        chain_force_extend_once_prob: float = 0.0,
        recovery_energy: int = 20,
        default_energy: int = 50,
        default_mood: int = 50,
        default_health: int = 50,
        good_night_cooldown_hours: int = 22,
    ):
        self.enabled = enabled
        self.slot_match_window_minutes = slot_match_window_minutes
        self.timezone = timezone
        self.min_event_interval_minutes = min_event_interval_minutes
        self.chain_max_depth = max(1, min(10, chain_max_depth))
        self.chain_force_extend_once_prob = chain_force_extend_once_prob
        self.recovery_energy = recovery_energy
        self.default_energy = default_energy
        self.default_mood = default_mood
        self.default_health = default_health
        self.good_night_cooldown_hours = good_night_cooldown_hours

    @classmethod
    def from_persona(cls, persona: "PersonaConfig") -> "CharacterLifeConfig":
        return cls(
            enabled=persona.character_life_enabled,
            slot_match_window_minutes=persona.character_life_jitter_minutes,
            timezone=persona.timezone,
            min_event_interval_minutes=persona.character_life_min_event_interval_minutes,
            chain_max_depth=persona.character_life_chain_max_depth,
            chain_force_extend_once_prob=persona.character_life_chain_force_extend_once_prob,
            default_energy=persona.character_life_default_energy,
            default_mood=persona.character_life_default_mood,
            default_health=persona.character_life_default_health,
            recovery_energy=persona.character_life_recovery_energy,
        )

    def now(self) -> datetime:
        return wall_now(self.timezone)


class CharacterLife:
    """角色生活管理器 — Phase 1: 纯状态管理，LLM 委托给 Agent"""

    def __init__(
        self,
        config: CharacterLifeConfig,
        data_store: PersonaDataStore,
        character: Character,
        dm_agent: Optional["DMAgent"] = None,
        character_agent: Optional["CharacterAgent"] = None,
    ):
        self.config = config
        self.data_store = data_store
        self.character = character
        self.dm_agent = dm_agent
        self.character_agent = character_agent
        # 当日计划槽位
        self._slot_minutes_today: Optional[List[Tuple[int, str]]] = None
        self._fired_slot_indices: Set[int] = set()
        self._last_event_date: Optional[str] = None
        self._ongoing_activities: List[OngoingActivity] = []
        self._today_jittered_start: Optional[int] = None
        self._today_jittered_end: Optional[int] = None
        self._chain_triggered_today: bool = False
        self._last_good_night_fired_at: Optional[datetime] = None
        self.boundary_receiver: Optional[BoundaryReceiver] = None
        self._boundaries_loaded = False
        self._state_lock = asyncio.Lock()

    def update_character(self, character: Character) -> None:
        """同步新的角色卡引用"""
        self.character = character

    def set_boundary_receiver(self, receiver: Optional[BoundaryReceiver]) -> None:
        if self._boundaries_loaded:
            raise RuntimeError("必须在 load_persistent_state 之前注入 boundary_receiver")
        self.boundary_receiver = receiver

    def _get_today_str(self) -> str:
        return self.config.now().strftime("%Y-%m-%d")

    @property
    def _spans_midnight(self) -> bool:
        if self._today_jittered_start is not None and self._today_jittered_end is not None:
            end = self._today_jittered_end
            start = self._today_jittered_start
            return end >= 1440 or start > end
        start_base = self.character.extensions.event_day_start_hour * 60
        end_base = self.character.extensions.event_day_end_hour * 60
        return (start_base >= end_base) or (end_base >= 1440)

    def _compute_daily_boundaries(self) -> tuple[int, int, random.Random]:
        today_str = self._get_today_str()
        seed_str = f"{today_str}:{self.character.name}"
        rng = random.Random(seed_str)
        start_jitter = self.character.extensions.event_day_start_jitter_minutes
        end_jitter = self.character.extensions.event_day_end_jitter_minutes
        start_base = self.character.extensions.event_day_start_hour * 60
        end_base = self.character.extensions.event_day_end_hour * 60
        start_time = start_base + rng.randint(-start_jitter, start_jitter)
        end_time = end_base + rng.randint(-end_jitter, end_jitter)
        if start_base < end_base and start_time >= end_time:
            end_time = start_time + 60
        return start_time, end_time, rng

    def _regenerate_slots_for_today(self) -> None:
        start, end, rng = self._compute_daily_boundaries()
        self._today_jittered_start = start
        self._today_jittered_end = end
        if self.boundary_receiver is not None:
            self.boundary_receiver.set_jittered_boundaries(start, end)
        else:
            logger.debug("boundary_receiver 未注入，跳过波动边界同步")
        min_interval = self.config.min_event_interval_minutes
        constrained_start = start + min_interval
        constrained_end = end - min_interval
        raw: List[Tuple[int, str]] = []
        raw.append((start, "wake_up"))
        raw.append((end, "good_night"))
        if constrained_start < constrained_end:
            raw_slots = self.character.extensions.generate_event_times(
                start_minute=constrained_start, end_minute=constrained_end, rng=rng
            )
            for s in raw_slots:
                raw.append((s, "system"))
        else:
            logger.warning(
                "角色 %s 当日可用区间过短（%02d:%02d-%02d:%02d，min_interval=%d），仅生成边界槽位",
                self.character.name, start // 60, start % 60, end // 60, end % 60, min_interval
            )
        raw.sort(key=lambda x: x[0])
        self._slot_minutes_today = [(m % 1440, t) for m, t in raw]
        logger.debug(
            "角色生活当日槽位 %s: %s (边界: %02d:%02d-%02d:%02d)",
            self._get_today_str(), self._slot_minutes_today,
            start // 60, start % 60, end // 60, end % 60,
        )

    def _reset_daily_state(self) -> None:
        today = self._get_today_str()
        if self._last_event_date == today:
            if self._slot_minutes_today is None:
                self._regenerate_slots_for_today()
            return
        if self._spans_midnight:
            has_unfired = (
                self._slot_minutes_today is not None
                and len(self._fired_slot_indices) < len(self._slot_minutes_today)
            )
            if has_unfired:
                self._last_event_date = today
                return
        self._fired_slot_indices.clear()
        self._chain_triggered_today = False
        self._today_jittered_start = None
        self._today_jittered_end = None
        self._regenerate_slots_for_today()
        self._last_event_date = today
        logger.debug("重置每日事件状态: {}", today)

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
        self._boundaries_loaded = True
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
            self._slot_minutes_today = []
            for item in sm:
                if item is None:
                    continue
                if isinstance(item, list) and len(item) == 2:
                    self._slot_minutes_today.append((int(item[0]), str(item[1])))
                else:
                    self._slot_minutes_today.append((int(item), "system"))
            self._slot_minutes_today = [
                (m % 1440, t) for m, t in self._slot_minutes_today
            ]
        else:
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
        self._chain_triggered_today = bool(data.get("chain_triggered"))
        lg = data.get("last_good_night_fired_at")
        if isinstance(lg, str):
            try:
                self._last_good_night_fired_at = datetime.fromisoformat(lg)
            except ValueError:
                self._last_good_night_fired_at = None
        js = data.get("jittered_start")
        je = data.get("jittered_end")
        if js is not None and je is not None:
            self._today_jittered_start = int(js)
            self._today_jittered_end = int(je)
        else:
            self._regenerate_slots_for_today()

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
            "last_good_night_fired_at": (
                self._last_good_night_fired_at.isoformat()
                if self._last_good_night_fired_at else None
            ),
            "jittered_start": self._today_jittered_start,
            "jittered_end": self._today_jittered_end,
        }
        await self.data_store.set_setting(
            PERSONA_SK_CHARACTER_LIFE,
            json.dumps(payload, ensure_ascii=False),
        )

    async def tick(self) -> Optional[List[Dict[str, Any]]]:
        if not self.config.enabled:
            return None
        self._reset_daily_state()
        self._cleanup_expired_activities()
        now = self.config.now()
        now_m = now.hour * 60 + now.minute
        slots = self._slot_minutes_today
        if not slots:
            return None
        win = max(1, self.config.slot_match_window_minutes)
        remaining = len(slots) - len(self._fired_slot_indices)
        for i, (slot_m, slot_type) in enumerate(slots):
            if i in self._fired_slot_indices:
                continue
            if (slot_type == "good_night"
                    and self._last_good_night_fired_at is not None
                    and (now - self._last_good_night_fired_at).total_seconds() < 3600 * self.config.good_night_cooldown_hours):
                continue
            dist = min(abs(now_m - slot_m), 1440 - abs(now_m - slot_m))
            if dist > win:
                continue
            logger.debug(
                f"tick 槽位触发: slot={i}/{len(slots)} type={slot_type} "
                f"plan={slot_m}min now={now_m}min remaining={remaining}"
            )
            event_chain = await self.generate_daily_event(slot_type)
            if event_chain:
                self._fired_slot_indices.add(i)
                if self._spans_midnight and len(self._fired_slot_indices) == len(slots):
                    self._fired_slot_indices.clear()
                    self._regenerate_slots_for_today()
                if slot_type == "good_night":
                    self._last_good_night_fired_at = now
                await self.save_persistent_state()
                return event_chain
        return None

    def _migrate_legacy_state(self, state: Any) -> None:
        if state.energy is None:
            state.energy = self.config.default_energy
        if state.mood is None:
            state.mood = self.config.default_mood
        if state.health is None:
            state.health = self.config.default_health

    @staticmethod
    def _clamp_delta(d: Optional[int]) -> int:
        if d is None:
            return 0
        return max(-20, min(20, d))

    @staticmethod
    def _serialize_raw_parts(event_raw: str, reaction_raw: str) -> str:
        raw_parts: Dict[str, Any] = {}
        for key, raw in [("event", event_raw), ("reaction", reaction_raw)]:
            if raw:
                try:
                    raw_parts[key] = json.loads(raw)
                except json.JSONDecodeError:
                    raw_parts[key] = raw
        return json.dumps(raw_parts, ensure_ascii=False) if raw_parts else ""

    async def generate_daily_event(self, slot_type: str = "system") -> List[Dict[str, Any]]:
        try:
            today = self._get_today_str()
            now = self.config.now()
            t0 = time.monotonic()
            async with self._state_lock:
                result = await self._generate_daily_event_impl(today, now, slot_type)
            elapsed_ge = time.monotonic() - t0
            if elapsed_ge > 30:
                logger.warning(
                    f"generate_daily_event 耗时 {elapsed_ge:.1f}s (>30s) slot_type={slot_type}"
                )
            return result
        except Exception as e:
            logger.exception("生成生活事件失败: {}", e)
            return []

    async def _generate_daily_event_impl(
        self, today: str, now, slot_type: str
    ) -> List[Dict[str, Any]]:
        """generate_daily_event 的 state-locked 实现

        Phase 1: 通过 DMAgent + CharacterAgent 而非 EventGenerationAgent。
        """
        try:
            # 获取角色状态
            character_state = await self.data_store.get_character_state()
            if character_state:
                self._migrate_legacy_state(character_state)
            else:
                character_state = CharacterState(
                    energy=self.config.default_energy,
                    mood=self.config.default_mood,
                    health=self.config.default_health,
                )
                await self.data_store.update_character_state(character_state)
                logger.info(
                    "首次初始化角色状态: energy=%s mood=%s health=%s",
                    character_state.energy, character_state.mood, character_state.health,
                )

            # 加载 DM 状态（用于 scratchpad 替代旧的 character_state.text）
            dm_state = await self.data_store.get_dm_state()

            # 加载一次上下文（链内复用）
            recent_diaries = await self._get_recent_diaries(3)
            today_db_events = await self._get_today_events()

            chain_events: List[Dict[str, str]] = []
            for e in today_db_events:
                evt_time = e.created_at.strftime("%H:%M") if e.created_at else "??:??"
                evt_iso = e.created_at.isoformat() if e.created_at else ""
                chain_events.append({"description": e.description, "time": evt_time, "created_at": evt_iso})

            # ── 事件-反应链循环 ──
            chain_depth = 0
            is_fallback = False
            event_chain: List[Dict[str, Any]] = []
            prev_follow_up: Optional[str] = None

            if slot_type == "wake_up":
                base_scenario = f"{self.character.scenario}\n\n【当前场景：角色刚刚醒来】"
            elif slot_type == "good_night":
                base_scenario = f"{self.character.scenario}\n\n【当前场景：角色准备入睡】"
            else:
                base_scenario = self.character.scenario

            while True:
                if chain_depth >= self.config.chain_max_depth:
                    break

                now = self.config.now()
                time_str = now.strftime("%H:%M")

                # 构建状态上下文
                ongoing = self.get_ongoing_activities()
                ongoing_context = "\n".join(
                    f"- 进行中: {a.description}" for a in ongoing
                ) if ongoing else ""
                state_context = (
                    f"体力{character_state.energy}/心情{character_state.mood}/健康{character_state.health}"
                )
                if ongoing_context:
                    state_context += "\n" + ongoing_context

                # 链续写场景（system_prompt 在 Conversation 生命周期内冻结，
                # 动态意图通过 follow_up_text → user_prompt 传递）
                chain_scenario = base_scenario

                # 构建日记上下文
                diary_context = ""
                if recent_diaries:
                    diary_context = "\n最近日记:\n" + "\n".join(
                        f"- {d[:100]}..." if len(d) > 100 else f"- {d}"
                        for d in recent_diaries[-3:]
                    )

                # 构建今日事件上下文
                events_context = ""
                if chain_events:
                    events_lines = []
                    for e in chain_events[-5:]:
                        created_at = e.get("created_at")
                        if created_at and now:
                            ts = format_timestamp(created_at, now)
                        else:
                            ts = e.get("time", "??:??")
                        desc = e.get("description", "")
                        events_lines.append(f"- [{ts}] {desc}")
                    events_context = (
                        "\n\n今天已经做过的事：\n"
                        + "\n".join(events_lines)
                        + "\n\n角色的一天还在继续。"
                    )


                now_str = now.strftime("%H:%M")
                date_str = now.strftime("%Y年%m月%d日")

                # ── 调用 DM Agent ──
                if self.dm_agent is None:
                    logger.error("dm_agent 未注入，无法生成事件")
                    break

                # 传递 scratchpad 给 DM Agent
                # 将 Character 的 follow_up 内容作为 DM 裁决上下文
                follow_up_text = prev_follow_up if chain_depth >= 1 else ""

                dm_context = {
                    "character_name": self.character.name,
                    "character_description": self.character.description,
                    "world": self.character.extensions.world,
                    "scenario": chain_scenario,
                    "state_text": state_context,
                    "diary_context": diary_context,
                    "events_context": events_context,
                    "now_str": now_str,
                    "date_str": date_str,
                    "slot_type": slot_type if chain_depth == 0 else "system",
                    "chain_depth": chain_depth,
                    "follow_up_text": follow_up_text,
                    "_scratchpad": dm_state.scratchpad,
                }
                dm_result = await self.dm_agent.run(dm_context)
                if not dm_result.success or not isinstance(dm_result.data, EventGenerationResult):
                    logger.warning("DM 生成事件失败，终止链")
                    break

                event_result: EventGenerationResult = dm_result.data

                # 单事件 delta 硬约束
                ed = CharacterLife._clamp_delta(event_result.energy_delta)
                md = CharacterLife._clamp_delta(event_result.mood_delta)
                hd = CharacterLife._clamp_delta(event_result.health_delta)

                # wake_up 体力 floor 保底
                if slot_type == "wake_up":
                    ed = max(event_result.energy_delta or 0, self.config.recovery_energy)

                # 更新角色状态
                character_state.energy = max(0, min(100, (character_state.energy if character_state.energy is not None else 50) + ed))
                character_state.mood = max(0, min(100, (character_state.mood if character_state.mood is not None else 50) + md))
                character_state.health = max(0, min(100, (character_state.health if character_state.health is not None else 50) + hd))
                await self.data_store.update_character_state(character_state)

                # ── 调用 Character Agent ──
                if self.character_agent is None:
                    logger.error("character_agent 未注入，无法生成反应")
                    break

                char_context = {
                    "mode": "reaction",
                    "event": event_result.description,
                    "character_name": self.character.name,
                    "character_description": self.character.description,
                    "today_events": list(chain_events),
                    "energy": character_state.energy,
                    "mood": character_state.mood,
                    "health": character_state.health,
                }
                char_result = await self.character_agent.react(char_context)
                if not char_result.success or not isinstance(char_result.data, EventReactionResult):
                    logger.warning("Character 反应生成失败，终止链")
                    break

                reaction_result: EventReactionResult = char_result.data

                # 桥接：Character 的 last_say_content 作为下一次 DM 裁决的 follow_up_text
                prev_follow_up = reaction_result.last_say_content

                combined_raw = CharacterLife._serialize_raw_parts(
                    event_result.raw_response, reaction_result.raw_response)

                # 保存事件到数据库
                await self.data_store.add_daily_event(
                    date=today,
                    event_type=slot_type if chain_depth == 0 else "system",
                    description=event_result.description,
                    reaction=reaction_result.reaction,
                    duration_minutes=event_result.duration_minutes,
                    system_prompt_digest=event_result.system_prompt_digest,
                    raw_response=combined_raw,
                    energy_delta=event_result.energy_delta,
                    mood_delta=event_result.mood_delta,
                    health_delta=event_result.health_delta,
                    context_summary=event_result.context_summary,
                )

                if event_result.duration_minutes > 0:
                    self._add_ongoing_activity(event_result.description, event_result.duration_minutes)

                event_chain.append({
                    "event_id": f"evt_{now.strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:8]}",
                    "description": event_result.description,
                    "reaction": reaction_result.reaction,
                    "duration_minutes": event_result.duration_minutes,
                    "time": time_str,
                    "slot_type": slot_type if chain_depth == 0 else "system",
                })

                logger.debug(
                    "[chain] {} @ {} depth={} energy={}({:+d}) mood={}({:+d}) health={}({:+d}) "
                    "has_follow_up={} fallback={}",
                    self.character.name, time_str, chain_depth + 1,
                    character_state.energy, ed,
                    character_state.mood, md,
                    character_state.health, hd,
                    reaction_result.has_follow_up,
                    is_fallback,
                )

                chain_events.append({"description": event_result.description, "time": time_str, "created_at": now.isoformat()})
                chain_depth += 1

                if reaction_result.has_follow_up:
                    if chain_depth >= 2:
                        self._chain_triggered_today = True
                    continue

                if chain_depth == 1 and not self._chain_triggered_today and not is_fallback:
                    if random.random() < self.config.chain_force_extend_once_prob:
                        is_fallback = True
                        logger.info("[chain] 触发保底续写: {}", self.character.name)
                        continue

                break

            if event_chain:
                logger.info(
                    "生成生活事件链: {}... (深度={}, 保底={})",
                    event_chain[0]["description"][:50],
                    chain_depth,
                    is_fallback,
                )
            return event_chain

        except Exception as e:
            logger.exception("生成生活事件失败 (impl): {}", e)
            return []

    async def _get_recent_diaries(self, days: int) -> List[str]:
        diaries = []
        for i in range(1, days + 1):
            date = (self.config.now() - timedelta(days=i)).strftime("%Y-%m-%d")
            diary = await self.data_store.get_diary(date)
            if diary:
                diaries.append(diary)
        return diaries

    async def _get_today_events(self) -> List[Any]:
        today = self._get_today_str()
        return await self.data_store.get_daily_events(today)

    def get_event_status(self) -> Dict[str, Any]:
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

    # ── SleepGate ──

    def _slot_fired(self, slot_type: str) -> bool:
        if not self._slot_minutes_today:
            return False
        for i, (_, st) in enumerate(self._slot_minutes_today):
            if st == slot_type and i in self._fired_slot_indices:
                return True
        return False

    async def is_awake(self) -> bool:
        async with self._state_lock:
            return self._is_awake_locked()

    def _is_awake_locked(self) -> bool:
        if self._today_jittered_start is None or self._today_jittered_end is None:
            return True
        now = self.config.now()
        now_m = now.hour * 60 + now.minute
        start = self._today_jittered_start
        end = self._today_jittered_end
        in_window: bool
        if self._spans_midnight:
            start_n = start % 1440
            end_n = end % 1440
            in_window = now_m >= start_n or now_m <= end_n
        else:
            # 当 _spans_midnight=False 时，_spans_midnight 属性保证 end >= start 恒成立
            in_window = start <= now_m <= end
        if not in_window:
            return False
        if self._slot_fired("good_night") and not self._slot_fired("wake_up"):
            return False
        return True

    # ── Spontaneous Event Injection ──

    async def _get_today_event_dicts(self) -> List[Dict[str, str]]:
        events = await self._get_today_events()
        result: List[Dict[str, str]] = []
        for e in events:
            evt_time = e.created_at.strftime("%H:%M") if e.created_at else "??:??"
            evt_iso = e.created_at.isoformat() if e.created_at else ""
            result.append({"description": e.description, "time": evt_time, "created_at": evt_iso})
        return result

    async def inject_spontaneous_event(self, action_description: str) -> bool:
        """注入自发事件，绕开槽位系统。

        Phase 1: 通过 DMAgent + CharacterAgent 而非 EventGenerationAgent。
        """
        async with self._state_lock:
            try:
                return await self._inject_spontaneous_event_impl(action_description)
            except Exception:
                logger.exception("[spontaneous] 注入失败")
                return False

    async def _inject_spontaneous_event_impl(self, action_description: str) -> bool:
        character_state = await self.data_store.get_character_state()
        if not character_state:
            return False
        self._migrate_legacy_state(character_state)

        # 获取 DM state scratchpad（替代旧的 character_state.text）
        dm_state = await self.data_store.get_dm_state()

        now = self.config.now()
        recent_diaries = await self._get_recent_diaries(3)
        today_event_dicts = await self._get_today_event_dicts()

        ongoing = self.get_ongoing_activities()
        ongoing_context = "\n".join(
            f"- 进行中: {a.description}" for a in ongoing
        ) if ongoing else ""
        state_context = (
            f"体力{character_state.energy}/心情{character_state.mood}/健康{character_state.health}"
        )
        if ongoing_context:
            state_context += "\n" + ongoing_context

        # 构建日记上下文
        diary_context = ""
        if recent_diaries:
            diary_context = "\n最近日记:\n" + "\n".join(
                f"- {d[:100]}..." if len(d) > 100 else f"- {d}"
                for d in recent_diaries[-3:]
            )

        events_context = ""
        if today_event_dicts:
            events_lines = []
            for e in today_event_dicts[-5:]:
                ts = e.get("time", "??:??")
                desc = e.get("description", "")
                events_lines.append(f"- [{ts}] {desc}")
            events_context = (
                "\n\n今天已经做过的事：\n"
                + "\n".join(events_lines)
                + "\n\n角色的一天还在继续。"
            )

        now_str = now.strftime("%H:%M")
        date_str = now.strftime("%Y年%m月%d日")

        # 意向文本（与计划事件路径一致）

        # ── 调用 DM Agent ──
        if self.dm_agent is None:
            logger.error("[spontaneous] dm_agent 未注入")
            return False

        dm_context = {
            "character_name": self.character.name,
            "character_description": self.character.description,
            "world": self.character.extensions.world,
            "scenario": f"{self.character.scenario}\n\n【当前场景：{action_description}】",
            "state_text": state_context,
            "diary_context": diary_context,
            "events_context": events_context,
            "now_str": now_str,
            "date_str": date_str,
            "slot_type": "system",
            "chain_depth": 0,
            "_scratchpad": dm_state.scratchpad,
        }
        dm_result = await self.dm_agent.run(dm_context)
        if not dm_result.success or not isinstance(dm_result.data, EventGenerationResult):
            logger.warning("[spontaneous] DM 生成事件失败")
            return False

        event_result: EventGenerationResult = dm_result.data

        ed = CharacterLife._clamp_delta(event_result.energy_delta)
        md = CharacterLife._clamp_delta(event_result.mood_delta)
        hd = CharacterLife._clamp_delta(event_result.health_delta)

        character_state.energy = max(0, min(100, (character_state.energy if character_state.energy is not None else 50) + ed))
        character_state.mood = max(0, min(100, (character_state.mood if character_state.mood is not None else 50) + md))
        character_state.health = max(0, min(100, (character_state.health if character_state.health is not None else 50) + hd))
        await self.data_store.update_character_state(character_state)

        # ── 调用 Character Agent ──
        if self.character_agent is None:
            logger.error("[spontaneous] character_agent 未注入")
            return False

        char_context = {
            "mode": "reaction",
            "event": event_result.description,
            "character_name": self.character.name,
            "character_description": self.character.description,
            "today_events": list(today_event_dicts),
            "energy": character_state.energy,
            "mood": character_state.mood,
            "health": character_state.health,
        }
        char_result = await self.character_agent.react(char_context)
        if not char_result.success or not isinstance(char_result.data, EventReactionResult):
            logger.warning("[spontaneous] Character 反应生成失败")
            return False

        reaction_result: EventReactionResult = char_result.data

        # pending_plan 已移除（由 Conversation 天内上下文替代）
        combined_raw = CharacterLife._serialize_raw_parts(
            event_result.raw_response, reaction_result.raw_response)

        try:
            await self.data_store.add_daily_event(
                date=self._get_today_str(),
                event_type="spontaneous",
                description=event_result.description,
                reaction=reaction_result.reaction,
                duration_minutes=event_result.duration_minutes,
                system_prompt_digest=event_result.system_prompt_digest,
                raw_response=combined_raw,
                energy_delta=event_result.energy_delta,
                mood_delta=event_result.mood_delta,
                health_delta=event_result.health_delta,
                context_summary=event_result.context_summary,
            )
        except Exception:
            logger.exception("[spontaneous] persist failed")
            return False

        if event_result.duration_minutes > 0:
            self._add_ongoing_activity(
                description=event_result.description,
                duration_minutes=event_result.duration_minutes,
            )

        logger.info(
            "[spontaneous] 注入成功 desc=%s energy=%+d mood=%+d health=%+d",
            event_result.description[:60],
            ed, md, hd,
        )
        return True

