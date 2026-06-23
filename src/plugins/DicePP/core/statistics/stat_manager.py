"""StatManager — per-key asyncio.Lock 原子读-改-写 for user_stat / group_stat

每个 user_id / group_id 维护一把独立的 asyncio.Lock，保证同一 key 上的
read-modify-write 串行化，消除多写路径间的丢失更新。
"""

import asyncio
from typing import Callable, Dict

from core.data.models import UserStat, GroupStat
from core.statistics.user_stat import UserStatInfo
from core.statistics.group_stat import GroupStatInfo


class StatManager:
    """为 user_stat / group_stat 提供原子化的读-改-写入口。"""

    def __init__(self, db):
        self._db = db
        self._user_locks: Dict[str, asyncio.Lock] = {}
        self._group_locks: Dict[str, asyncio.Lock] = {}
        self._locks_guard = asyncio.Lock()

    # ------------------------------------------------------------------
    # 锁管理：惰性创建 + 双重检查
    # ------------------------------------------------------------------

    async def _get_lock(
        self, pool: Dict[str, asyncio.Lock], key: str
    ) -> asyncio.Lock:
        """惰性创建并返回 key 对应的 asyncio.Lock。

        双重检查：外层无 await 快路径避免不必要竞争；
        内层在 _locks_guard 下保证只创建一次。
        """
        lock = pool.get(key)
        if lock is not None:
            return lock
        async with self._locks_guard:
            lock = pool.get(key)
            if lock is None:
                lock = asyncio.Lock()
                pool[key] = lock
            return lock

    # ------------------------------------------------------------------
    # user_stat 原子更新
    # ------------------------------------------------------------------

    async def update_user_stat(
        self, user_id: str, updater: Callable[[UserStatInfo], None]
    ) -> None:
        """在锁内对 user_stat 执行原子 read-modify-write。

        updater 接收一个 UserStatInfo 实例，可在其上进行任意修改。
        锁内自动完成 读取→反序列化→updater→序列化→写回。
        """
        lock = await self._get_lock(self._user_locks, user_id)
        async with lock:
            row = await self._db.user_stat.get(user_id)
            stat = UserStatInfo()
            if row is not None and row.data:
                try:
                    stat.deserialize(row.data)
                except Exception:
                    stat = UserStatInfo()
            updater(stat)
            await self._db.user_stat.upsert(
                UserStat(user_id=user_id, data=stat.serialize())
            )

    # ------------------------------------------------------------------
    # group_stat 原子更新
    # ------------------------------------------------------------------

    async def update_group_stat(
        self, group_id: str, updater: Callable[[GroupStatInfo], None]
    ) -> None:
        """在锁内对 group_stat 执行原子 read-modify-write。"""
        lock = await self._get_lock(self._group_locks, group_id)
        async with lock:
            row = await self._db.group_stat.get(group_id)
            stat = GroupStatInfo()
            if row is not None and row.data:
                try:
                    stat.deserialize(row.data)
                except Exception:
                    stat = GroupStatInfo()
            updater(stat)
            await self._db.group_stat.upsert(
                GroupStat(group_id=group_id, data=stat.serialize())
            )

    # ------------------------------------------------------------------
    # 锁内只读（供需要基于最新数据做决策但不修改的场景）
    # ------------------------------------------------------------------

    async def read_user_stat(self, user_id: str) -> UserStatInfo:
        """在 per-key 锁内读取并返回 user_stat 的反序列化副本。

        调用方拿到的是锁内读取的最新值，不会与并发原子更新产生 TOCTOU。
        """
        lock = await self._get_lock(self._user_locks, user_id)
        async with lock:
            row = await self._db.user_stat.get(user_id)
            stat = UserStatInfo()
            if row is not None and row.data:
                try:
                    stat.deserialize(row.data)
                except Exception:
                    stat = UserStatInfo()
            return stat

    async def read_group_stat(self, group_id: str) -> GroupStatInfo:
        """在 per-key 锁内读取并返回 group_stat 的反序列化副本。"""
        lock = await self._get_lock(self._group_locks, group_id)
        async with lock:
            row = await self._db.group_stat.get(group_id)
            stat = GroupStatInfo()
            if row is not None and row.data:
                try:
                    stat.deserialize(row.data)
                except Exception:
                    stat = GroupStatInfo()
            return stat

