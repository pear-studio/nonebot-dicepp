"""ShareScheduler — 主动分享日程调度器

管理角色按时间日程驱动的主动分享消息：
- 早晚仪式（早安 / 晚安）
- 自定义中间时间点

依赖 TargetSelector 获取发送目标，通过回调触发 ChatOrchestrator。

生命周期：
  1. Simulator.tick() 每 60s 调用 share_scheduler.tick()
  2. tick() 检测是否命中某个日程时间点的 jitter 窗口
  3. 命中后从 TargetSelector 读取 force 目标，按 scope 去重
  4. 逐个调用 _trigger_callback (ChatOrchestrator.trigger_proactive)
"""

from __future__ import annotations

import json
import random as random_module
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Optional

from utils.logger import logger
from utils.time import get_clock

from ..data.persist_keys import PERSONA_SK_SHARE_SCHEDULER
from ..data.store import PersonaDataStore
from ..character.models import Character
from .conversation_scope import ConversationScope
from .models import ShareTarget
from .protocols import BoundaryReceiver
from .target import TargetSelector

if TYPE_CHECKING:
    from core.config.pydantic_models import PersonaConfig


class ShareScheduler(BoundaryReceiver):
    """主动分享日程调度器。

    按时间日程（早安/晚安/自定义时段）驱动角色主动分享消息。
    每 60s 由 Simulator.tick() 调用一次，检测是否到达日程时间点。

    实现 BoundaryReceiver 接口以接收 CharacterLife 同步的活跃边界。
    """

    def __init__(
        self,
        config: "PersonaConfig",
        character: Character,
        target_selector: TargetSelector,
        data_store: PersonaDataStore,
        get_trigger_callback: Optional[Callable[..., Awaitable[Any]]] = None,
    ) -> None:
        """初始化 ShareScheduler。

        Args:
            config: PersonaConfig（用于读取 proactive_share_schedule_* 字段）。
            character: 角色实例。
            target_selector: 目标选择器，用于获取分享目标。
            data_store: 持久化数据存储。
            get_trigger_callback: 可选，预注入触发回调。
        """
        self.config = config
        self.character = character
        self.target_selector = target_selector
        self.data_store = data_store

        # 已触发的时间点标签集（同一天同一标签只触发一次）
        self._fired_times: set[str] = set()

        # Tick 节流
        self._last_tick: Optional[datetime] = None
        self._tick_interval = timedelta(seconds=60)

        # 跨天记录
        self._last_event_date: Optional[str] = None

        # 触发回调（由 ChatOrchestrator.trigger_proactive 注入）
        self._trigger_callback: Optional[Callable[..., Awaitable[Any]]] = None
        if get_trigger_callback is not None:
            self._trigger_callback = get_trigger_callback

        # CharacterLife 同步的 jittered 活跃边界
        self._jittered_start_minute: Optional[int] = None
        self._jittered_end_minute: Optional[int] = None

        # 持久化脏数据检查
        self._last_persisted_blob: Optional[str] = None

        # 空配置日志标记（每个实例只记录一次）
        self._empty_config_logged: bool = False

    # ── 公共接口 ──────────────────────────────────────────

    def set_trigger_callback(self, callback: Callable[..., Awaitable[Any]]) -> None:
        """设置触发回调（ChatOrchestrator.trigger_proactive）。"""
        self._trigger_callback = callback

    def update_character(self, character: Character) -> None:
        """同步新的角色卡引用（热更新支持）"""
        self.character = character

    def set_jittered_boundaries(self, start_minute: int, end_minute: int) -> None:
        """由 CharacterLife 调用，同步今日波动后的活跃边界。"""
        self._jittered_start_minute = start_minute
        self._jittered_end_minute = end_minute

    # ── 核心 tick ─────────────────────────────────────────

    async def tick(self) -> None:
        """核心 tick 方法，每 60s 由 Simulator 调用一次。

        流程：
          1. 检查总开关
          2. 60s 节流
          3. 跨天重置 _fired_times
          4. 计算今天的日程时间点
          5. 遍历日程点，检查活跃状态和 jitter 窗口
          6. 触发符合条件的日程点
          7. 持久化状态（finally）
        """
        # 1. 总开关
        if not self.config.proactive_share_schedule_enabled:
            return

        # 2. 60s 节流
        now = self._now()
        if self._last_tick is not None and now - self._last_tick < self._tick_interval:
            return

        try:
            self._last_tick = now

            # 3. 跨天重置
            today = self._get_today_str()
            if self._last_event_date != today:
                logger.debug("ShareScheduler(%s): 跨天重置日程 %s", self.character.name, today)
                self._last_event_date = today
                self._fired_times.clear()

            # 4. 计算日程时间点
            schedule = self._compute_schedule_times()

            # 9.8: 空配置检测
            if not schedule:
                if not self._empty_config_logged:
                    logger.info(
                        "ShareScheduler(%s) 已启用但未配置日程时间点 "
                        "(morning_enabled=%s, evening_enabled=%s, schedule_times=%s)",
                        self.character.name,
                        self.config.proactive_share_schedule_morning_enabled,
                        self.config.proactive_share_schedule_evening_enabled,
                        self.config.proactive_share_schedule_times,
                    )
                    self._empty_config_logged = True
                return

            # 5. 当前分钟数
            now_m = now.hour * 60 + now.minute

            # 6. 遍历日程点
            for label, center_minute in schedule:
                if label in self._fired_times:
                    continue

                # 活跃检查
                if not self._is_character_active(for_time_label=label):
                    continue

                # Jitter 窗口命中检查
                if not self._in_jitter_window(center_minute, now_m):
                    continue

                # 决定是否触发
                if not self._should_trigger(now_m, center_minute, label):
                    continue

                # 移交 _execute_schedule_point 内部标记 fired
                # （target selection 失败时会回退标记，避免日程点永久丢失）
                await self._execute_schedule_point(label, center_minute)

        finally:
            # 7. 持久化（无论成功还是异常都落盘）
            try:
                await self._persist_state()
            except Exception as e:
                logger.error(
                    "ShareScheduler(%s) 持久化异常: %s",
                    self.character.name,
                    e,
                    exc_info=True,
                )

    # ── 时间工具 ──────────────────────────────────────────

    def _now(self) -> datetime:
        """获取当前时间（使用统一时钟）。"""
        return get_clock().now()

    def _get_today_str(self) -> str:
        """返回今天日期字符串 YYYY-MM-DD。"""
        return self._now().strftime("%Y-%m-%d")

    # ── 日程计算 ──────────────────────────────────────────

    def _compute_schedule_times(self) -> list[tuple[str, int]]:
        """返回今天的所有日程时间点。

        Returns:
            [(label, minute_of_day)] 列表。label 格式：
            - "morning" — 早安
            - "evening" — 晚安
            - "midday_HH:MM" — 中间时段
        """
        times: list[tuple[str, int]] = []

        # 早安（角色卡 event_day_start_hour + 5 分钟）
        if self.config.proactive_share_schedule_morning_enabled:
            start_hour = self.character.extensions.event_day_start_hour
            if start_hour is not None and start_hour > 0:
                times.append(("morning", (start_hour * 60 + 5) % 1440))
            else:
                logger.warning(
                    "ShareScheduler(%s): 早安已启用但 event_day_start_hour=%s，跳过早安",
                    self.character.name,
                    start_hour,
                )

        # 晚安（角色卡 event_day_end_hour - 5 分钟）
        if self.config.proactive_share_schedule_evening_enabled:
            end_hour = self.character.extensions.event_day_end_hour
            if end_hour is not None and end_hour > 0:
                times.append(("evening", (end_hour * 60 - 5) % 1440))
            else:
                logger.warning(
                    "ShareScheduler(%s): 晚安已启用但 event_day_end_hour=%s，跳过晚安",
                    self.character.name,
                    end_hour,
                )

        # 中间时间点
        for t_str in self.config.proactive_share_schedule_times:
            try:
                h, m = map(int, t_str.split(":", 1))
                times.append((f"midday_{t_str}", (h * 60 + m) % 1440))
            except (ValueError, IndexError):
                logger.warning(
                    "ShareScheduler(%s): 无效时间格式 %r，跳过",
                    self.character.name,
                    t_str,
                )

        return times

    # ── 活跃检查 ──────────────────────────────────────────

    def _is_character_active(self, for_time_label: str = "") -> bool:
        """检查角色当前是否处于活跃时间窗口。

        策略（设计文档 2.3.1 / 9.6）：
        - "morning" / "evening"：使用角色卡原始活跃小时。
          避免 jittered 边界推迟了活跃窗口导致早安被跳过。
        - "midday_*"：优先使用 CharacterLife 同步的 jittered 边界，
          无 jittered 边界时回退到角色卡原始小时。
        """
        now = self._now()
        now_m = now.hour * 60 + now.minute

        if for_time_label in ("morning", "evening"):
            # 早晚始终使用角色卡原始 hours
            start_m = (self.character.extensions.event_day_start_hour or 0) * 60
            end_m = (self.character.extensions.event_day_end_hour or 23) * 60
            return self._minute_in_range(now_m, start_m % 1440, end_m % 1440)

        # 中间时段：优先使用 jittered 边界
        if self._jittered_start_minute is not None and self._jittered_end_minute is not None:
            return self._minute_in_range(
                now_m, self._jittered_start_minute, self._jittered_end_minute
            )

        # 回退：角色卡原始小时
        start_m = (self.character.extensions.event_day_start_hour or 0) * 60
        end_m = (self.character.extensions.event_day_end_hour or 23) * 60
        return self._minute_in_range(now_m, start_m % 1440, end_m % 1440)

    @staticmethod
    def _minute_in_range(now_m: int, start: int, end: int) -> bool:
        """检查分钟数 now_m 是否在 [start, end] 区间内（支持跨午夜）。

        start 和 end 均已规整到 0-1439。当 start == end 时视为全天活跃。
        """
        if start == end:
            return True
        if start < end:
            return start <= now_m <= end
        # start > end: 跨午夜，窗口为 [start, 1439] ∪ [0, end]
        return now_m >= start or now_m <= end

    # ── Jitter 窗口 ───────────────────────────────────────

    def _in_jitter_window(self, center: int, now_m: int) -> bool:
        """检查 now_m 是否在日程时间点的 jitter 窗口内。

        窗口范围: [center - jitter, center + jitter]（分钟级，支持午夜包裹）。
        jitter = config.proactive_share_schedule_jitter_minutes。
        """
        jitter = self.config.proactive_share_schedule_jitter_minutes
        if jitter <= 0:
            return now_m == center

        low = (center - jitter) % 1440
        high = (center + jitter) % 1440

        if low <= high:
            return low <= now_m <= high
        # 窗口跨午夜
        return now_m >= low or now_m <= high

    def _should_trigger(self, now_m: int, center: int, label: str) -> bool:
        """判断当前 tick 是否应触发分享。

        使用每日种子 + 偏移量构造确定性 RNG，确保同一天内决策可复现。
        - 窗口末尾（now_m == high）强制触发。
        - 窗口内以概率 1/窗口宽度 触发（平均每窗口触发一次）。
        - jitter <= 0 时仅在 center 精确命中时触发。
        """
        jitter = self.config.proactive_share_schedule_jitter_minutes
        if jitter <= 0:
            return now_m == center

        low = (center - jitter) % 1440
        high = (center + jitter) % 1440

        # 窗口末尾强制触发（兜底），使用 >= 防止 tick 延迟跳过 high 分钟导致丢失
        if now_m >= high:
            return True

        # 窗口宽度（分钟数）
        if low <= high:
            window_width = high - low + 1
        else:
            window_width = (1440 - low) + high + 1

        # 每个 tick 独立判定，概率 = 1 / 窗口宽度
        prob = 1.0 / max(window_width, 1)

        # 每日确定性种子：日期 + 角色名 + 标签 + 窗口内偏移
        today = self._get_today_str()
        seed_base = f"{today}_{self.character.name}_{label}"

        # 当前 tick 在窗口内的偏移量
        offset = (now_m - low) % 1440 if low > high else now_m - low

        tick_rng = random_module.Random(f"{seed_base}_offset_{offset}")
        return tick_rng.random() < prob

    # ── 执行触发 ──────────────────────────────────────────

    async def _execute_schedule_point(self, label: str, center_minute: int) -> None:
        """执行一个日程时间点的分享逻辑。

        1. 从 TargetSelector 获取 force 目标（异常时回退 fired 标记，允许重试）
        2. 标记 label 已触发（防同一 tick 重入）
        3. 按 ConversationScope 去重
        4. 为每个目标构建 trigger_message 并调用回调
           （per-target 个别失败不回退标记——其他 target 已成功发送）
        """
        if self._trigger_callback is None:
            logger.warning(
                "ShareScheduler(%s): trigger_callback 未设置（label=%s），跳过触发",
                self.character.name,
                label,
            )
            return

        # 读取所有候选目标（异常时回退标记，允许下个 tick 重试）
        try:
            all_targets = await self.target_selector.select_share_targets()
        except Exception:
            logger.exception(
                "ShareScheduler(%s): target_selector.select_share_targets 异常（label=%s），"
                "回退标记允许重试",
                self.character.name,
                label,
            )
            self._fired_times.discard(label)
            return

        # 只取 force 策略（白名单）
        force_targets = [t for t in all_targets if t.policy == "force"]

        if not force_targets:
            logger.debug(
                "ShareScheduler(%s): 日程点 %s 无 force 目标，跳过",
                self.character.name,
                label,
            )
            return

        # 目标选择成功，标记已触发（防同一 tick 重入，且 per-target 失败不回退）
        self._fired_times.add(label)

        # 按 ConversationScope 去重
        seen_scopes: set[ConversationScope] = set()
        unique_targets: list[tuple[ConversationScope, ShareTarget]] = []

        for target in force_targets:
            scope = (
                ConversationScope.for_group(target.group_id)
                if target.is_group
                else ConversationScope.for_private(target.user_id)
            )
            if scope in seen_scopes:
                continue
            seen_scopes.add(scope)
            unique_targets.append((scope, target))

        # 为每个去重后的目标触发
        for scope, target in unique_targets:
            try:
                trigger_msg = self._build_trigger_message(label, target)

                # 私聊时填充目标名称
                if "{name}" in trigger_msg and target.user_id:
                    target_name = await self._get_target_name(target.user_id)
                    trigger_msg = trigger_msg.replace("{name}", target_name)

                await self._trigger_callback(
                    scope,
                    trigger_msg,
                    user_id=target.user_id,
                    group_id=target.group_id,
                )

                logger.info(
                    "ShareScheduler(%s): 日程点 %s → (user=%s, group=%s) 触发成功",
                    self.character.name,
                    label,
                    target.user_id,
                    target.group_id,
                )
            except Exception as e:
                logger.error(
                    "ShareScheduler(%s): 日程点 %s → (user=%s, group=%s) 触发异常: %s",
                    self.character.name,
                    label,
                    target.user_id,
                    target.group_id,
                    e,
                    exc_info=True,
                )

    def _build_trigger_message(self, label: str, target: ShareTarget) -> str:
        """根据时间点类型和目标类型构建触发提示消息。

        设计文档 5: 群聊和私聊使用不同的提示语。
        """
        if label == "morning":
            if target.is_group:
                return "（天亮了，跟大家说早安。）"
            return "（天亮了，跟{name}说早安。）"

        if label == "evening":
            if target.is_group:
                return "（夜深了，跟大家说晚安。）"
            return "（夜深了，跟{name}说晚安。）"

        # midday_*
        if target.is_group:
            return "（和大家聊聊吧。）"
        return "（和{name}聊聊吧。）"

    async def _get_target_name(self, user_id: str) -> str:
        """获取用户的显示名称，失败时返回 user_id。"""
        try:
            profile = await self.data_store.get_user_profile(user_id)
            if profile and profile.nickname:
                return profile.nickname
        except Exception:
            pass
        return user_id

    # ── 持久化 ────────────────────────────────────────────

    async def _persist_state(self) -> None:
        """持久化调度器状态（含脏数据检查）。"""
        today = self._get_today_str()
        payload = {
            "date": today,
            "fired_times": sorted(self._fired_times),
        }
        blob = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        if blob == self._last_persisted_blob:
            return
        await self.data_store.set_setting(PERSONA_SK_SHARE_SCHEDULER, blob)
        self._last_persisted_blob = blob

    async def load_persistent_state(self) -> None:
        """从 data_store 加载持久化状态。

        若持久化的日期与今天相同则恢复 _fired_times；
        否则丢弃旧状态（跨天自动重置）。
        """
        raw = await self.data_store.get_setting(PERSONA_SK_SHARE_SCHEDULER)
        if not raw:
            return

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning(
                "ShareScheduler(%s): 持久化数据解析失败，丢弃",
                self.character.name,
            )
            return

        today = self._get_today_str()
        saved_date = data.get("date", "")

        if saved_date == today:
            fired = data.get("fired_times", [])
            self._fired_times = set(fired)
            self._last_event_date = today
            logger.debug(
                "ShareScheduler(%s): 恢复持久化状态，已触发时间点: %s",
                self.character.name,
                fired,
            )
        else:
            logger.debug(
                "ShareScheduler(%s): 持久化日期 %s != 今天 %s，丢弃旧状态",
                self.character.name,
                saved_date,
                today,
            )

        self._last_persisted_blob = raw
