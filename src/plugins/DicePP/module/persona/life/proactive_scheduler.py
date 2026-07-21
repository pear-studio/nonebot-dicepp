"""主动消息调度器

管理定时问候、想念触发、事件分享等主动消息
"""
from typing import List, Dict, Optional, Set, Tuple, Any, Callable, Awaitable, TYPE_CHECKING
from datetime import datetime, timedelta
import asyncio
import json
import random
from utils.logger import logger
import re

from ..data.store import PersonaDataStore
from ..data.persist_keys import PERSONA_SK_SCHEDULER
from ..data.models import RelationshipState, DEFAULT_RELATION_LABELS
from ..character.models import Character
from ..game.decay import DecayCalculator
from utils.time import format_timestamp, format_relative_time
from .protocols import BoundaryReceiver
from .models import ShareTarget
from .utils import effective_for_proactive
from .proactive_config import ProactiveConfig

if TYPE_CHECKING:
    from .target import TargetSelector
    from ..llm.coordinator import LLMCallCoordinator
    from .character_agent import CharacterAgent


class ProactiveScheduler(BoundaryReceiver):
    """主动消息调度器"""

    # 想念触发概率阶段固定表（冷淡/疏远/友好/默契/亲密）
    _MISS_PROBABILITY = {0: 0.0, 1: 0.5, 2: 0.7, 3: 0.9, 4: 1.0}

    def __init__(
        self,
        config: ProactiveConfig,
        data_store: PersonaDataStore,
        character: Character,
        target_selector: "TargetSelector",
        coordinator: "LLMCallCoordinator",
        character_agent: Optional["CharacterAgent"] = None,
        decay_calculator: Optional[DecayCalculator] = None,
    ):
        self.config = config
        self.data_store = data_store
        self.character = character
        self.character_agent = character_agent
        self._decay_calculator = decay_calculator
        self.target_selector = target_selector
        self.coordinator = coordinator

        self._last_tick: Optional[datetime] = None
        self._tick_interval = timedelta(seconds=60)  # 60秒节流

        self._last_proactive_time: Dict[str, datetime] = {}  # target_key -> last_time

        self._last_event_date: Optional[str] = None

        # 在首次异步使用时再创建，避免绑定到错误的事件循环
        self._share_lock: Optional[asyncio.Lock] = None
        self._llm_semaphore: Optional[asyncio.Semaphore] = None
        self._last_persisted_scheduler_blob: Optional[str] = None

        # jittered 活跃边界（由 CharacterLife 同步，优先于角色卡原始小时）
        self._jittered_start_minute: Optional[int] = None
        self._jittered_end_minute: Optional[int] = None

    def update_character(self, character: Character) -> None:
        """同步新的角色卡引用"""
        self.character = character
        if hasattr(self, 'target_selector') and self.target_selector is not None:
            self.target_selector.update_character(character)

    def set_jittered_boundaries(self, start_minute: int, end_minute: int) -> None:
        """由 CharacterLife 调用，同步今日波动后的活跃边界。"""
        self._jittered_start_minute = start_minute
        self._jittered_end_minute = end_minute

    def _get_share_lock(self) -> asyncio.Lock:
        if self._share_lock is None:
            self._share_lock = asyncio.Lock()
        return self._share_lock

    def _get_llm_semaphore(self) -> asyncio.Semaphore:
        if self._llm_semaphore is None:
            self._llm_semaphore = asyncio.Semaphore(
                self.config.share_message_concurrent
            )
        return self._llm_semaphore

    def _now(self) -> datetime:
        from utils.time import get_clock
        return get_clock().now()

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
            self._last_event_date = today
            logger.debug(f"重置每日调度状态: {today}")

    def _is_character_active(self) -> bool:
        """检查当前是否在角色活跃时间

        优先使用 CharacterLife 同步的 jittered 边界（分钟级精度），
        未同步时回退到角色卡原始小时边界。

        活跃窗口使用包含结束边界（inclusive end），与 CharacterLife._is_awake_locked 保持一致。
        """
        now = self._now()
        now_m = now.hour * 60 + now.minute

        if self._jittered_start_minute is not None and self._jittered_end_minute is not None:
            start = self._jittered_start_minute
            end = self._jittered_end_minute
            if start < end:
                if end >= 1440:
                    # end_hour >= 24：活跃窗跨午夜
                    return now_m >= start or now_m <= (end % 1440)
                return start <= now_m <= end
            elif start > end:
                return now_m >= start or now_m <= end
            else:
                return True

        # 回退：角色卡原始小时边界（使用包含结束边界）
        hour = now.hour
        start_h = self.character.extensions.event_day_start_hour
        end_h = self.character.extensions.event_day_end_hour

        if end_h >= 24:
            return hour >= start_h or hour < (end_h % 24)
        elif start_h < end_h:
            return start_h <= hour < end_h
        elif start_h > end_h:
            return hour >= start_h or hour < end_h
        else:
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

        miss_you / share 功能已由 ShareScheduler + ChatOrchestrator.trigger_proactive 替代。
        后续改造 miss_you 为 ChatOrchestrator 路径时恢复本方法，届时整体删除旧实现。

        Returns:
            待发送的消息列表
        """
        if True:
            return []
        if not self.config.enabled:
            return []

    async def _persist_miss_switch(self, rel: RelationshipState, now: datetime) -> None:
        """记录想念已发出，打开衰减开关；异常时回滚。"""
        original_miss_at = rel.last_miss_sent_at
        rel.last_miss_sent_at = now
        try:
            await self.data_store.update_relationship(rel)
        except Exception:
            rel.last_miss_sent_at = original_miss_at
            raise

    async def _check_missed_users(self) -> List[Dict]:
        """检查并触发想念消息"""
        return []
        if not self.config.miss_enabled:
            return []

        messages = []
        now = self._now()
        min_idle = timedelta(hours=self.config.miss_min_hours)

        try:
            relationships = await self._get_active_relationships()
            logger.debug(f"想念检查: 活跃关系数={len(relationships)}")

            for rel in relationships:
                # reputation 每日恢复（在门控之前，通过 store 原子方法持久化）
                await self.data_store.try_daily_reputation_recovery(rel, now)

                eff = effective_for_proactive(rel, self._decay_calculator, self.character)
                # 信誉门控
                if eff.reputation < self.config.reputation_refuse_threshold:
                    logger.debug(
                        f"想念跳过(信誉低): user={rel.user_id}, "
                        f"reputation={eff.reputation:.1f}"
                    )
                    continue
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

                user_id = rel.user_id

                # R10: 检查 DB 中是否已记录过想念（防多实例/重启重复发送）
                # last_miss_sent_at 非 None 表示已发出想念且用户尚未回应
                if rel.last_miss_sent_at is not None:
                    logger.debug(
                        f"想念跳过(已发过): user={user_id}, "
                        f"last_miss_sent_at={rel.last_miss_sent_at}"
                    )
                    continue

                # 检查最小间隔（内存字典，同一实例内的节流）
                if not self._can_send_to_key(f"user:{user_id}"):
                    logger.debug(f"想念跳过(间隔): user={user_id}")
                    continue
                # Phase 3: 检查用户是否关闭了主动消息
                if await self.data_store.is_user_muted(user_id):
                    logger.debug(f"想念跳过(静音): user={user_id}")
                    continue

                # 阶段固定概率表
                relation_level, _ = eff.get_relation_level(DEFAULT_RELATION_LABELS)
                probability = self._MISS_PROBABILITY.get(relation_level, 0.0)
                if random.random() > probability:
                    logger.debug(
                        f"想念跳过(概率): user={user_id}, level={relation_level}, p={probability:.2f}"
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
                # R9: buffered 意味着消息已排队，也应 break（避免同一 tick 触发多条）
                if msg and msg.get("__coordinator_buffered"):
                    # 即使 buffered 也记录想念已发出，打开衰减开关
                    await self._persist_miss_switch(rel, now)
                    break
                if msg:
                    messages.append(msg)
                    logger.info(
                        f"想念触发: user={user_id}, idle={idle_hours:.1f}h, "
                        f"score={eff.composite_score:.1f}, event={event_desc[:40]}"
                    )

                    # 记录想念已发出，打开衰减开关
                    await self._persist_miss_switch(rel, now)

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
            logger.error(f"获取活跃关系失败: {e}", exc_info=True)
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

    # 改为实例方法以通过 self._now() 获取时区时间
    def _format_recent_history(self, messages, limit: int = 5) -> str:
        """将 Message 列表格式化为精简对话摘要，附带时间戳。"""
        if not messages:
            return "（无）"
        lines = []
        now = self._now()
        role_map = {"user": "玩家", "assistant": "我", "system": "系统", "tool": "工具"}
        for msg in messages[-limit:]:
            role_label = role_map.get(msg.role, "玩家")
            content = msg.content
            if len(content) > 50:
                content = content[:47] + "..."
            ts = getattr(msg, "created_at", None)
            prefix = format_timestamp(ts, now)
            rel = format_relative_time(ts, now)
            extra = f" {rel}" if rel else ""
            full_prefix = f"{prefix}{extra}"
            if full_prefix:
                lines.append(f"- [{full_prefix}] {role_label}: {content}")
            else:
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
        return None
        if not self.character_agent:
            return None

        try:
            # 获取目标上下文
            user_profile = await self.data_store.get_user_profile(target.user_id)
            rel = await self.data_store.get_relationship(target.user_id)

            relation_label = ""
            relationship_score = 0.0
            if rel:
                relationship_score = rel.composite_score
                labels = self.character.get_relation_labels()
                _, relation_label = rel.get_relation_level(labels)

            if target.group_id:
                recent_msgs = await self.data_store.get_group_messages(
                    target.group_id, limit=self.config.share_context_history_limit
                )
            else:
                recent_msgs = await self.data_store.get_recent_messages(
                    target.user_id, limit=self.config.share_context_history_limit
                )

            share_examples = self.character.extensions.share_message_examples

            # 获取角色当前状态与今日事件
            character_state = await self.data_store.get_character_state()
            today = self._get_today_str()
            today_db_events = await self.data_store.get_daily_events(today)
            today_events = []
            for e in today_db_events:
                evt_time = e.created_at.strftime("%H:%M") if e.created_at else "??:??"
                today_events.append({"description": e.description, "time": evt_time})

            # 构建 share context dict
            share_context = {
                "mode": "share",
                "event_description": event_description,
                "reaction": reaction,
                "character_name": self.character.name,
                "character_description": self.character.description,
                "target_user_id": target.user_id,
                "relationship_score": relationship_score,
                "relation_label": relation_label,
                "user_profile_facts": self._format_user_profile_facts(user_profile),
                "recent_history": self._format_recent_history(recent_msgs, self.config.share_context_history_limit),
                "message_type": message_type,
                "environment": environment,
                "share_message_examples": share_examples,
                "energy": character_state.energy if character_state else None,
                "mood": character_state.mood if character_state else None,
                "health": character_state.health if character_state else None,
                "today_events": today_events if today_events else None,
                "current_intention": None,  # [DEPRECATED] 已从 CharacterState 移除
            }

            result = await self.character_agent.share(share_context)
            if not result.success or result.data is None:
                return None
            message = result.data
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(
                f"构建分享上下文失败: user={target.user_id}, group={target.group_id}, error={e}",
                exc_info=True,
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
        供 LifeSimulator 调用。

        Returns:
            成功创建的消息列表
        """
        return []
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

        async def _gen_for_target(target: ShareTarget) -> Optional[Dict]:
            key = self._target_key(target)

            async def share_call_fn(_messages: List[str]):
                async with self._get_llm_semaphore():
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

            result = await self.coordinator.submit(
                key, None, share_call_fn, continue_on_buffered=False
            )
            if result.status == "success":
                return result.value
            return None

        results = await asyncio.gather(
            *[_gen_for_target(t) for t in valid_targets],
        )

        messages: List[Dict] = []
        for r in results:
            if r is not None:
                messages.append(r)

        return messages

    async def shutdown(self) -> None:
        """关闭调度器。"""
        logger.debug("ProactiveScheduler 已关闭")

    async def _create_miss_you_message(
        self,
        target: ShareTarget,
        event_description: str,
        reaction: str,
    ) -> Optional[Dict]:
        """创建想念消息，通过 coordinator 串行化 LLM 调用。"""
        return None
        key = self._target_key(target)
        now = self._now()

        async def miss_call_fn(_messages: List[str]):
            async with self._get_llm_semaphore():
                msg_dict = await self._build_and_generate_share_message(
                    target=target,
                    event_description=event_description,
                    reaction=reaction,
                    message_type="miss_you",
                    environment="private",
                )
            if msg_dict:
                self._last_proactive_time[key] = now
            return msg_dict

        try:
            result = await self.coordinator.submit(
                key, None, miss_call_fn, continue_on_buffered=False
            )
            if result.status == "success":
                return result.value
            if result.status == "buffered":
                # R9: buffered 意味着消息已排队，应 break 外层循环
                return {"__coordinator_buffered": True}
            return None
        except Exception as e:
            logger.error(f"创建想念消息失败: {e}", exc_info=True)
            return None

    def get_status(self) -> Dict:
        """获取调度器状态（用于调试）"""
        return {
            "enabled": self.config.enabled,
            "is_character_active": self._is_character_active(),
            "last_proactive_count": len(self._last_proactive_time),
        }


