"""
CommandContextResolver — 统一命令上下文构建器 (Task 3.1 + 3.2)

职责：
  - 为每次 process_msg 调用构建独立的 CommandContext 实例
  - CommandContext 持有 per-invocation 读缓存，保证同一调用周期内数据一致性
  - 解析层（CommandTextParser）不触库；上下文层按需读取配置与状态

设计原则（design.md 决策5）：
  - 每次 process_msg 入口构建新实例，不跨调用共享
  - per-invocation 读缓存：首次 await get(key) 访问 DB，后续返回缓存
  - asyncio 安全：每个协程持有独立实例，无需额外锁
"""
from __future__ import annotations

from typing import Any, Dict, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from core.bot import Bot
    from core.communication import MessageMetaData


class CommandContext:
    """
    单次命令调用的上下文容器。

    持有 per-invocation 读缓存，保证同一 process_msg 调用周期内
    对同一上下文键的多次读取返回相同快照。

    使用方式::

        ctx = await CommandContextResolver.resolve(bot, meta)
        group_cfg = await ctx.group_config()   # 首次访问 DB
        group_cfg2 = await ctx.group_config()  # 命中缓存，返回相同快照

    """

    def __init__(self, bot: "Bot", meta: "MessageMetaData"):
        self._bot = bot
        self._meta = meta
        self._cache: Dict[str, Any] = {}

        # 常用只读属性（同步计算，无 DB 访问）
        self.is_group: bool = bool(meta.group_id)
        self.is_private: bool = not self.is_group
        self.user_id: str = meta.user_id
        self.group_id: str = meta.group_id
        self.nickname: str = meta.nickname
        self.permission: int = meta.permission

        # 私聊使用 __user__<user_id> 作为 group_config 的 key
        self.config_key: str = (
            f"__user__{meta.user_id}" if self.is_private else meta.group_id
        )

    # ------------------------------------------------------------------
    # 缓存辅助
    # ------------------------------------------------------------------

    def _cached(self, key: str) -> tuple[bool, Any]:
        """检查缓存，返回 (hit, value)。"""
        if key in self._cache:
            return True, self._cache[key]
        return False, None

    def _store(self, key: str, value: Any) -> Any:
        """写入缓存并返回 value。"""
        self._cache[key] = value
        return value

    # ------------------------------------------------------------------
    # 上下文读取方法（按需加载，命中缓存后无额外 DB 开销）
    # ------------------------------------------------------------------

    async def group_config(self):
        """
        读取当前群/私聊的 GroupConfig。
        首次调用访问 DB，同一 invocation 内后续调用返回缓存快照。
        """
        cache_key = f"group_config:{self.config_key}"
        hit, val = self._cached(cache_key)
        if hit:
            return val
        result = await self._bot.db.group_config.get(self.config_key)
        return self._store(cache_key, result)

    async def group_config_data(self) -> Dict[str, Any]:
        """读取 GroupConfig.data 字典，不存在时返回空字典。"""
        cfg = await self.group_config()
        return cfg.data if cfg else {}

    async def get_config_value(self, field: str, default: Any = None) -> Any:
        """从群/私聊配置中读取指定字段值。"""
        data = await self.group_config_data()
        return data.get(field, default)

    async def karma(self):
        """读取当前用户的 Karma 记录（per-invocation 缓存）。"""
        cache_key = f"karma:{self.user_id}:{self.group_id}"
        hit, val = self._cached(cache_key)
        if hit:
            return val
        result = await self._bot.db.karma.get(self.user_id, self.group_id)
        return self._store(cache_key, result)

    # ------------------------------------------------------------------
    # 直通属性（不需要缓存）
    # ------------------------------------------------------------------

    @property
    def bot(self) -> "Bot":
        return self._bot

    @property
    def meta(self) -> "MessageMetaData":
        return self._meta

    def __repr__(self) -> str:
        return (
            f"CommandContext(user={self.user_id!r}, "
            f"group={self.group_id!r}, "
            f"is_private={self.is_private}, "
            f"cache_keys={list(self._cache.keys())})"
        )


class CommandContextResolver:
    """
    CommandContext 的工厂类。

    用法::

        ctx = await CommandContextResolver.resolve(bot, meta)
        # 在 process_msg 开头调用，获取本次 invocation 的独立上下文

    """

    @staticmethod
    async def resolve(bot: "Bot", meta: "MessageMetaData") -> CommandContext:
        """
        为一次 process_msg 调用构建独立的 CommandContext。

        此方法本身是 async 以保持接口一致性，未来如需预加载
        高频上下文字段（如群配置）可在此处并发 gather。
        """
        return CommandContext(bot=bot, meta=meta)
