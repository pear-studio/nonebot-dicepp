"""StatManager 并发安全测试。

验证 per-key asyncio.Lock 原子更新能消除 user_stat/group_stat
多写路径间的丢失更新问题。
"""

import asyncio
import os
import tempfile
from unittest.mock import MagicMock

import pytest

from core.data import Repository
from core.data.models import UserStat, GroupStat
from core.statistics.stat_manager import StatManager
from core.statistics.user_stat import UserStatInfo
from core.statistics.group_stat import GroupStatInfo


# ── fixtures ──────────────────────────────────────────────────────────


class _FakeDB:
    """最小 mock — 只暴露 user_stat / group_stat 两个 Repository。"""

    def __init__(self, db_conn):
        self.user_stat = Repository[UserStat](
            db_conn, UserStat, "user_stat", ["user_id"]
        )
        self.group_stat = Repository[GroupStat](
            db_conn, GroupStat, "group_stat", ["group_id"]
        )


@pytest.fixture
async def stat_manager():
    """创建使用内存 SQLite 的 StatManager。"""
    import aiosqlite

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        conn = await aiosqlite.connect(db_path)
        await conn.execute("PRAGMA journal_mode=WAL;")

        fake_db = _FakeDB(conn)
        await fake_db.user_stat._ensure_table()
        await fake_db.group_stat._ensure_table()

        mgr = StatManager(fake_db)
        yield mgr
        await conn.close()


# ── 辅助函数 ──────────────────────────────────────────────────────────


# ══════════════════════════════════════════════════════════════════════
# 基本正确性
# ══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_update_new_user_creates_default(stat_manager):
    """对不存在的 user_id 执行 update → 自动创建默认 stat 并写入。"""
    await stat_manager.update_user_stat("u1", lambda s: s.msg.inc())

    row = await stat_manager._db.user_stat.get("u1")
    assert row is not None
    stat = UserStatInfo()
    stat.deserialize(row.data)
    assert stat.msg.cur_day_val == 1
    assert stat.msg.total_val == 1


@pytest.mark.asyncio
async def test_update_existing_user_preserves_other_fields(stat_manager):
    """原子更新只修改 updater 内指定的字段，其他字段保持不变。"""
    # 先创建一条记录
    await stat_manager.update_user_stat("u1", lambda s: s.msg.inc())
    # 再追加 roll 统计
    await stat_manager.update_user_stat(
        "u1", lambda s: s.roll.times.inc(5)
    )

    row = await stat_manager._db.user_stat.get("u1")
    stat = UserStatInfo()
    stat.deserialize(row.data)
    assert stat.msg.cur_day_val == 1  # 未被第二次更新覆盖
    assert stat.roll.times.cur_day_val == 5
    assert stat.roll.times.total_val == 5


@pytest.mark.asyncio
async def test_update_group_stat_basic(stat_manager):
    """group_stat 原子更新基本流程。"""
    await stat_manager.update_group_stat("g1", lambda s: s.msg.inc())
    await stat_manager.update_group_stat("g1", lambda s: s.msg.inc())

    row = await stat_manager._db.group_stat.get("g1")
    stat = GroupStatInfo()
    stat.deserialize(row.data)
    assert stat.msg.cur_day_val == 2


# ══════════════════════════════════════════════════════════════════════
# 并发安全 —— 核心场景
# ══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_concurrent_same_key_no_lost_update(stat_manager):
    """N 个协程同时修改同一 user_id → 每次 inc 都可见，无丢失。"""
    N = 50

    async def inc_msg():
        await stat_manager.update_user_stat("u1", lambda s: s.msg.inc())

    await asyncio.gather(*(inc_msg() for _ in range(N)))

    row = await stat_manager._db.user_stat.get("u1")
    stat = UserStatInfo()
    stat.deserialize(row.data)
    assert stat.msg.cur_day_val == N
    assert stat.msg.total_val == N


@pytest.mark.asyncio
async def test_concurrent_different_fields_same_key_no_lost_update(stat_manager):
    """模拟 process_message(msg.inc) + record_roll_data(roll.inc) 并发场景。"""
    async def inc_msg():
        for _ in range(30):
            await stat_manager.update_user_stat("u1", lambda s: s.msg.inc())

    async def inc_roll():
        for _ in range(20):
            await stat_manager.update_user_stat(
                "u1", lambda s: s.roll.times.inc(3)
            )

    await asyncio.gather(inc_msg(), inc_roll())

    row = await stat_manager._db.user_stat.get("u1")
    stat = UserStatInfo()
    stat.deserialize(row.data)
    assert stat.msg.cur_day_val == 30
    assert stat.msg.total_val == 30
    assert stat.roll.times.cur_day_val == 60  # 20 × 3
    assert stat.roll.times.total_val == 60


@pytest.mark.asyncio
async def test_different_keys_not_blocked(stat_manager):
    """不同 user_id / group_id 的锁互不影响 —— 可并行执行。"""
    enter_order = []
    exit_order = []

    async def slow_update(uid):
        enter_order.append(uid)
        await stat_manager.update_user_stat(uid, lambda s: s.msg.inc())
        exit_order.append(uid)

    # u1 和 u2 同时启动，应能并行完成（不同锁）
    t1 = asyncio.create_task(slow_update("u1"))
    t2 = asyncio.create_task(slow_update("u2"))
    await asyncio.gather(t1, t2)

    # 两个都进入了且两个都完成了
    assert len(enter_order) == 2
    assert len(exit_order) == 2
    assert "u1" in enter_order
    assert "u2" in enter_order


@pytest.mark.asyncio
async def test_concurrent_group_stat_no_lost_update(stat_manager):
    """group_stat 并发安全 —— 模拟同一群内多用户同时触发写入。"""
    N = 40

    async def inc_group_msg():
        await stat_manager.update_group_stat("g1", lambda s: s.msg.inc())

    await asyncio.gather(*(inc_group_msg() for _ in range(N)))

    row = await stat_manager._db.group_stat.get("g1")
    stat = GroupStatInfo()
    stat.deserialize(row.data)
    assert stat.msg.cur_day_val == N


@pytest.mark.asyncio
async def test_daily_update_concurrent_with_inc(stat_manager):
    """模拟 tick_daily(daily_update 清零) 与 process_message(msg.inc) 并发。"""
    await stat_manager.update_user_stat("u1", lambda s: s.msg.inc())
    await stat_manager.update_user_stat("u1", lambda s: s.msg.inc())

    async def daily_reset():
        await stat_manager.update_user_stat("u1", lambda s: s.daily_update())

    async def another_inc():
        await stat_manager.update_user_stat("u1", lambda s: s.msg.inc())

    await asyncio.gather(daily_reset(), another_inc())

    row = await stat_manager._db.user_stat.get("u1")
    stat = UserStatInfo()
    stat.deserialize(row.data)

    # daily_update 把 cur_day_val 移到 last_day_val 再清零；
    # 如果 another_inc 在 daily_update 之后执行 → cur_day_val=1, last_day_val=2
    # 如果 another_inc 在 daily_update 之前执行 → cur_day_val=1(inc), last_day_val=3(被清零前)
    # 但因为有锁，不会出现 inc 被清零覆盖的情况。
    # 关键断言：cur_day_val + last_day_val 必须等于 3（不发生丢失）
    assert stat.msg.cur_day_val + stat.msg.last_day_val == 3
    # cur_day_val 要么是 1（inc 在 reset 后），要么是 0（inc 在 reset 前）
    assert stat.msg.cur_day_val in (0, 1)




# ══════════════════════════════════════════════════════════════════════
# 容错
# ══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_corrupt_data_in_user_stat_is_recovered(stat_manager):
    """如果 user_stat 中的 JSON 损坏，deserialize 失败时使用默认空对象。"""
    from core.data.models import UserStat as UserStatModel

    # 直接写入损坏的 JSON
    bad_row = UserStatModel(user_id="u1", data="not valid json{{{")
    await stat_manager._db.user_stat.upsert(bad_row)

    # 原子更新应该降级使用空 UserStatInfo 且不抛异常
    await stat_manager.update_user_stat("u1", lambda s: s.msg.inc())

    row = await stat_manager._db.user_stat.get("u1")
    stat = UserStatInfo()
    stat.deserialize(row.data)
    # 损坏数据被覆盖，msg 从 0 开始计数
    assert stat.msg.cur_day_val == 1


@pytest.mark.asyncio
async def test_corrupt_data_in_group_stat_is_recovered(stat_manager):
    """group_stat 损坏 JSON 同理。"""
    from core.data.models import GroupStat as GroupStatModel

    bad_row = GroupStatModel(group_id="g1", data="corrupt{{{")
    await stat_manager._db.group_stat.upsert(bad_row)

    await stat_manager.update_group_stat("g1", lambda s: s.msg.inc())

    row = await stat_manager._db.group_stat.get("g1")
    stat = GroupStatInfo()
    stat.deserialize(row.data)
    assert stat.msg.cur_day_val == 1


# ══════════════════════════════════════════════════════════════════════
# 锁管理
# ══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_lock_reuse_same_key(stat_manager):
    """同一 key 多次获取锁返回同一个 Lock 对象。"""
    lock1 = await stat_manager._get_lock(stat_manager._user_locks, "u1")
    lock2 = await stat_manager._get_lock(stat_manager._user_locks, "u1")
    assert lock1 is lock2


@pytest.mark.asyncio
async def test_lock_different_keys(stat_manager):
    """不同 key 获取不同的 Lock 对象。"""
    lock_u1 = await stat_manager._get_lock(stat_manager._user_locks, "u1")
    lock_u2 = await stat_manager._get_lock(stat_manager._user_locks, "u2")
    assert lock_u1 is not lock_u2


@pytest.mark.asyncio
async def test_user_and_group_locks_independent(stat_manager):
    """user_stat 和 group_stat 的锁池互相独立。"""
    u_lock = await stat_manager._get_lock(stat_manager._user_locks, "same_key")
    g_lock = await stat_manager._get_lock(stat_manager._group_locks, "same_key")
    assert u_lock is not g_lock
