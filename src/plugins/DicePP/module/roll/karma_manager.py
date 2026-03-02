from __future__ import annotations

"""
业力骰子核心管理器，负责配置读取、历史队列维护与掷骰修正逻辑。
"""

import random
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Deque, Dict, List, Optional, Tuple, TYPE_CHECKING

from core.data import DataChunkBase, custom_data_chunk

from .karma_runtime import DiceRuntime, reset_runtime, set_runtime

if TYPE_CHECKING:
    from core.bot import Bot

# 数据持久化标识
DC_KARMA = "karma_dice"


@custom_data_chunk(identifier=DC_KARMA)
class _(DataChunkBase):
    """为 DataManager 注册业力骰子配置存储块。"""

    def __init__(self):
        super().__init__()


# 常量配置
DEFAULT_PERCENTAGE = 60
DEFAULT_WINDOW = 20
ADVANTAGE_ROLLS = 3
PRECISION_MIN_RATIO = 0.05
FORCE_DIFF_FLOOR = 15.0
AVERAGE_TOLERANCE = 0.5
MAX_WINDOW = 200

MODE_ALIASES: Dict[str, Tuple[str, ...]] = {
    "custom": ("custom", "自定义"),
    "balanced": ("balanced", "均衡", "均衡稳定"),
    "dramatic": ("dramatic", "戏剧化"),
    "hero": ("hero", "主角光环"),
    "grim": ("grim", "冷酷现实"),
    "stable": ("stable", "高斯稳定"),
}

ENGINE_ALIASES: Dict[str, Tuple[str, ...]] = {
    "advantage": ("advantage", "优势判定", "adv", "优势"),
    "precise": ("precise", "精确加权", "precision", "精确"),
}

MODE_DISPLAY: Dict[str, str] = {
    "custom": "自定义",
    "balanced": "均衡模式",
    "dramatic": "戏剧化",
    "hero": "主角光环",
    "grim": "冷酷现实",
    "stable": "高斯稳定",
}

ENGINE_DISPLAY: Dict[str, str] = {
    "advantage": "优势判定",
    "precise": "精确加权",
}

HERO_TARGET = 65
HERO_WINDOW = 15
HERO_FORCE_THRESHOLD = 40
GRIM_TARGET = 40
GRIM_WINDOW = 25
GRIM_FORCE_THRESHOLD = 95
BALANCED_TARGET = 55
BALANCED_WINDOW = 15


@dataclass
class KarmaConfig:
    """封装群级配置，便于读写。"""

    is_enabled: bool = False
    mode: str = "custom"
    engine: str = "precise"
    custom_percentage: int = DEFAULT_PERCENTAGE
    custom_roll_count: int = DEFAULT_WINDOW
    intro_sent: bool = False

    def to_dict(self) -> Dict[str, object]:
        return {
            "is_enabled": self.is_enabled,
            "mode": self.mode,
            "engine": self.engine,
            "custom_percentage": self.custom_percentage,
            "custom_roll_count": self.custom_roll_count,
            "intro_sent": self.intro_sent,
        }

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, object]]) -> "KarmaConfig":
        if not data:
            return cls()
        return cls(
            is_enabled=bool(data.get("is_enabled", False)),
            mode=str(data.get("mode", "custom")),
            engine=str(data.get("engine", "advantage")),
            custom_percentage=int(data.get("custom_percentage", DEFAULT_PERCENTAGE)),
            custom_roll_count=int(data.get("custom_roll_count", DEFAULT_WINDOW)),
            intro_sent=bool(data.get("intro_sent", False)),
        )


class KarmaState:
    """维护群内掷骰历史，用于计算滚动期望。"""

    def __init__(self):
        self.window: int = DEFAULT_WINDOW
        self.history: Deque[float] = deque()

    def resize(self, window: int):
        window = max(1, min(window, MAX_WINDOW))
        if window == self.window:
            return
        self.window = window
        while len(self.history) > self.window:
            self.history.popleft()

    def append(self, value: float):
        self.history.append(value)
        while len(self.history) > self.window:
            self.history.popleft()

    def average(self) -> float:
        if not self.history:
            return 50.0
        return sum(self.history) / len(self.history)

    def last(self) -> Optional[float]:
        if not self.history:
            return None
        return self.history[-1]

    def tail(self, count: int) -> List[float]:
        if count <= 0 or not self.history:
            return []
        return list(self.history)[-count:]


class _KarmaRuntime(DiceRuntime):
    """运行时代理，供 roll_a_dice 在上下文中调用。"""

    def __init__(self, manager: "KarmaDiceManager", group_id: str, user_id: str):
        self._manager = manager
        self._group_id = group_id
        self._user_id = user_id

    def roll(self, dice_type: int) -> int:
        return self._manager.generate_value(self._group_id, self._user_id, dice_type)


class KarmaDiceManager:
    """业力骰子核心调度器。"""

    def __init__(self, bot: "Bot"):
        self.bot = bot
        self._state: Dict[str, Dict[str, Dict[int, KarmaState]]] = {}

    # ---------- 配置与状态维护 ----------
    def _get_config(self, group_id: str) -> KarmaConfig:
        default_cfg = KarmaConfig().to_dict()
        raw = self.bot.data_manager.get_data(DC_KARMA, [group_id], default_val=default_cfg, get_ref=True)
        return KarmaConfig.from_dict(raw)

    def _save_config(self, group_id: str, config: KarmaConfig) -> None:
        self.bot.data_manager.set_data(DC_KARMA, [group_id], config.to_dict())

    def _get_state(self, group_id: str, user_id: str, dice_type: int) -> KarmaState:
        group_states = self._state.setdefault(group_id, {})
        user_states = group_states.setdefault(user_id, {})
        state = user_states.setdefault(dice_type, KarmaState())
        config = self._get_config(group_id)
        _, window = self._get_effective_params(config)
        state.resize(window)
        return state

    def reset_history(self, group_id: str, user_id: Optional[str] = None) -> None:
        """清空指定群聊或指定用户的业力历史。"""
        if user_id is None:
            self._state[group_id] = {}
        else:
            group_states = self._state.setdefault(group_id, {})
            group_states[user_id] = {}

    def is_enabled(self, group_id: Optional[str]) -> bool:
        if not group_id:
            return False
        return self._get_config(group_id).is_enabled

    def enable(self, group_id: str) -> Tuple[bool, bool]:
        config = self._get_config(group_id)
        was_enabled = config.is_enabled
        if not was_enabled:
            config.is_enabled = True
            first_intro = not config.intro_sent
            config.intro_sent = True
            config.mode = "balanced"
            config.engine = "precise"
            self._save_config(group_id, config)
            self.reset_history(group_id)
            return True, first_intro
        return False, False

    def disable(self, group_id: str) -> bool:
        config = self._get_config(group_id)
        if not config.is_enabled:
            return False
        config.is_enabled = False
        self._save_config(group_id, config)
        return True

    def set_engine(self, group_id: str, engine: str) -> bool:
        engine_norm = self.normalize_engine(engine)
        if not engine_norm:
            raise ValueError("未知的业力引擎")
        config = self._get_config(group_id)
        if config.engine == engine_norm:
            return False
        config.engine = engine_norm
        self._save_config(group_id, config)
        return True

    def set_mode(self, group_id: str, mode: str) -> bool:
        mode_norm = self.normalize_mode(mode)
        if not mode_norm:
            raise ValueError("未知的业力模式")
        config = self._get_config(group_id)
        if config.mode == mode_norm:
            return False
        config.mode = mode_norm
        if mode_norm == "balanced":
            config.engine = "precise"
        self._save_config(group_id, config)
        self.reset_history(group_id)
        return True

    def set_custom_params(self, group_id: str, percentage: int, window: int) -> None:
        percentage = max(1, min(percentage, 100))
        window = max(1, min(window, MAX_WINDOW))
        config = self._get_config(group_id)
        config.custom_percentage = percentage
        config.custom_roll_count = window
        config.mode = "custom"
        self._save_config(group_id, config)
        self.reset_history(group_id)

    def get_status(self, group_id: str, user_id: str) -> Dict[str, object]:
        config = self._get_config(group_id)
        target, window = self._get_effective_params(config)

        group_states = self._state.get(group_id, {})
        user_states = group_states.get(user_id, {})

        user_dice_stats: Dict[int, Dict[str, float]] = {}
        user_total_sum = 0.0
        user_total_count = 0
        for dice_face, dice_state in user_states.items():
            history = list(dice_state.history)
            history_len = len(history)
            if history_len:
                avg = sum(history) / history_len
                user_dice_stats[dice_face] = {
                    "average": round(avg, 2),
                    "count": history_len,
                }
                user_total_sum += sum(history)
                user_total_count += history_len

        user_average = round(user_total_sum / user_total_count, 2) if user_total_count else None
        user_history_len = user_total_count

        total_count = 0
        total_sum = 0.0
        active_users = 0
        for dice_state_map in group_states.values():
            user_has_data = False
            for dice_state in dice_state_map.values():
                history_len = len(dice_state.history)
                if history_len:
                    user_has_data = True
                    total_count += history_len
                    total_sum += sum(dice_state.history)
            if user_has_data:
                active_users += 1
        group_average = round(total_sum / total_count, 2) if total_count else None

        snapshot = {
            "enabled": config.is_enabled,
            "mode": config.mode,
            "mode_display": MODE_DISPLAY.get(config.mode, config.mode),
            "engine": config.engine,
            "engine_display": ENGINE_DISPLAY.get(config.engine, config.engine),
            "target": target,
            "window": window,
            "user_average": user_average,
            "user_history_len": user_history_len,
            "group_average": group_average,
            "group_user_count": active_users,
            "user_dice_stats": user_dice_stats,
        }
        if config.mode in ("dramatic", "stable"):
            snapshot["engine_display"] = "已绕过核心引擎"
        return snapshot

    # ---------- 运行时控制 ----------
    @contextmanager
    def activate(self, group_id: Optional[str], user_id: Optional[str]):
        if not group_id or not user_id or not self.is_enabled(group_id):
            yield False
            return
        runtime = _KarmaRuntime(self, group_id, user_id)
        token = set_runtime(runtime)
        try:
            yield True
        finally:
            reset_runtime(token)

    def generate_value(self, group_id: str, user_id: str, dice_type: int) -> int:
        config = self._get_config(group_id)
        state = self._get_state(group_id, user_id, dice_type)
        target, _ = self._get_effective_params(config)

        direction, forced, current_avg = self._determine_direction(config.mode, state, target)

        if dice_type < 1:
            dice_type = 1

        if config.mode == "dramatic":
            value = self._roll_dramatic(dice_type)
        elif config.mode == "stable":
            value = self._roll_stable(dice_type)
        else:
            value = self._roll_with_engine(config.engine, dice_type, direction, forced, target, current_avg)

        norm = self._normalize(value, dice_type)
        state.append(norm)
        return value

    # ---------- 辅助算法 ----------
    def _roll_with_engine(
        self,
        engine: str,
        dice_type: int,
        direction: Optional[str],
        forced: bool,
        target: float,
        current_avg: float,
    ) -> int:
        if dice_type <= 1:
            return dice_type
        if not direction:
            return self._roll_standard(dice_type)

        if engine == "advantage":
            return self._roll_advantage(dice_type, direction)

        diff = abs(target - current_avg)
        if forced:
            diff = max(diff, FORCE_DIFF_FLOOR)
        return self._roll_precise(dice_type, direction, diff)

    def _roll_standard(self, dice_type: int) -> int:
        return random.randint(1, dice_type)

    def _roll_advantage(self, dice_type: int, direction: str) -> int:
        rolls = [random.randint(1, dice_type) for _ in range(ADVANTAGE_ROLLS)]
        return max(rolls) if direction == "up" else min(rolls)

    def _roll_precise(self, dice_type: int, direction: str, diff: float) -> int:
        ratio = max(diff / 100.0, PRECISION_MIN_RATIO)
        ratio = min(ratio, 0.95)
        faces = list(range(1, dice_type + 1))
        if dice_type == 1:
            return 1
        weights: List[float] = []
        for face in faces:
            norm = (face - 1) / (dice_type - 1)
            if direction == "up":
                weight = 1.0 + ratio * norm
            else:
                weight = 1.0 + ratio * (1.0 - norm)
            weights.append(max(weight, 0.01))
        total = sum(weights)
        pick = random.random() * total
        cumulative = 0.0
        for face, weight in zip(faces, weights):
            cumulative += weight
            if pick <= cumulative:
                return face
        return faces[-1]

    def _roll_dramatic(self, dice_type: int) -> int:
        if dice_type <= 1:
            return dice_type
        edge = max(1, dice_type // 5)
        roll = random.random()
        if roll < 0.45:
            return random.randint(1, edge)
        if roll < 0.9:
            return dice_type - random.randint(0, edge - 1)
        return random.randint(1, dice_type)

    def _roll_stable(self, dice_type: int) -> int:
        if dice_type <= 1:
            return dice_type
        rolls = [random.randint(1, dice_type) for _ in range(3)]
        average = round(sum(rolls) / 3)
        return min(max(average, 1), dice_type)

    def _normalize(self, value: int, dice_type: int) -> float:
        if dice_type <= 0:
            return 0.0
        return float(value) / float(dice_type) * 100.0

    def _determine_direction(
        self,
        mode: str,
        state: KarmaState,
        target: float,
    ) -> Tuple[Optional[str], bool, float]:
        current_avg = state.average()
        forced = False
        direction: Optional[str] = None

        if mode == "hero":
            recent = state.tail(3)
            if len(recent) == 3 and all(val < HERO_FORCE_THRESHOLD for val in recent):
                direction = "up"
                forced = True
        elif mode == "grim":
            last = state.last()
            if last is not None and last > GRIM_FORCE_THRESHOLD:
                direction = "down"
                forced = True

        if not direction:
            if current_avg + AVERAGE_TOLERANCE < target:
                direction = "up"
            elif current_avg - AVERAGE_TOLERANCE > target:
                direction = "down"

        return direction, forced, current_avg

    def _get_effective_params(self, config: KarmaConfig) -> Tuple[int, int]:
        if config.mode == "balanced":
            return BALANCED_TARGET, BALANCED_WINDOW
        if config.mode == "hero":
            return HERO_TARGET, HERO_WINDOW
        if config.mode == "grim":
            return GRIM_TARGET, GRIM_WINDOW
        return config.custom_percentage, config.custom_roll_count

    # ---------- 输入规整 ----------
    def normalize_mode(self, text: str) -> Optional[str]:
        lowered = text.lower()
        for key, aliases in MODE_ALIASES.items():
            if lowered in aliases:
                return key
        return None

    def normalize_engine(self, text: str) -> Optional[str]:
        lowered = text.lower()
        for key, aliases in ENGINE_ALIASES.items():
            if lowered in aliases:
                return key
        return None


_MANAGER_CACHE: Dict[int, KarmaDiceManager] = {}


def get_karma_manager(bot: "Bot") -> KarmaDiceManager:
    """为给定 Bot 获得单例管理器。"""
    key = id(bot)
    if key not in _MANAGER_CACHE:
        _MANAGER_CACHE[key] = KarmaDiceManager(bot)
    return _MANAGER_CACHE[key]
