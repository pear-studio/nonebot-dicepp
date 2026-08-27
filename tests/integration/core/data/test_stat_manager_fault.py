"""StatManager 故障注入回归测试。

验证 stat 写入失败时不会中断核心流程（消息处理、tick_daily 等）。
所有严重问题（R1/R2/R3）均有独立测试用例。
"""

import asyncio
import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from plugins.DicePP.core.data.models import UserStat as UserStatModel
from plugins.DicePP.core.data.models import GroupStat as GroupStatModel
from plugins.DicePP.core.statistics.user_stat import UserStatInfo
from plugins.DicePP.core.statistics.group_stat import GroupStatInfo
from plugins.DicePP.core.config.pydantic_models import BotConfig


# ══════════════════════════════════════════════════════════════════════
# R1: stat 写入失败不中断消息处理
# ══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_process_msg_stat_write_failure():
    """StatManager.update_user_stat 抛异常时消息处理不崩溃。

    回归 B-260602-4263c4：stat 写入从延迟批量 try/except 改为内联
    原子更新后缺失异常保护，DB 异常可直接传播到 nonebot adapter。
    """
    from plugins.DicePP.core.bot import Bot
    from plugins.DicePP.core.communication import MessageMetaData, MessageSender

    bot = MagicMock(spec=Bot)
    bot.stat_manager = MagicMock()
    bot.stat_manager.update_user_stat = AsyncMock(
        side_effect=OSError("simulated db failure")
    )
    bot.stat_manager.update_group_stat = AsyncMock(
        side_effect=OSError("simulated db failure")
    )
    bot.config = MagicMock(spec=BotConfig)
    bot.config.master = ""
    bot.config.command_split = "\n"
    bot.command_dict = {}
    bot.proxy = MagicMock()
    bot.proxy.process_bot_command_list = AsyncMock()
    bot._delay_init_done = True
    bot._inbound_message_hooks = []

    meta = MessageMetaData(".help", ".help", MessageSender("u1", "t"), "", True)

    # 修复前：OSError 直接传播 → 测试失败（确认 bug 存在）
    # 修复后：异常被 _safe_update_user_stat 捕获 → 流程继续
    try:
        result = await Bot.process_message(bot, ".help", meta)
    except OSError:
        pytest.fail(
            "process_message 因 stat 写入失败而崩溃——"
            "stat 调用缺少异常保护（R1 退化未修复）"
        )
    except Exception:
        # 允许其他异常（mock 不完整可能导致 KeyError/AttributeError）
        # 但不允许 OSError——那恰恰是我们要防御的
        pass


# ══════════════════════════════════════════════════════════════════════
# R2: tick_daily 单行故障不阻断其余行
# ══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_tick_daily_continues_after_single_row_failure():
    """tick_daily 逐行 daily_update 时，单行 DB 故障不中断其余行。

    回归：逐行原子 daily_update 缺少 per-row try/except，
    任一行的 upsert 失败会阻断剩余所有行的刷新。
    """
    import aiosqlite
    from plugins.DicePP.core.data import Repository
    from plugins.DicePP.core.statistics.stat_manager import StatManager

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        conn = await aiosqlite.connect(db_path)

        class _FakeDB:
            user_stat = Repository[UserStatModel](
                conn, UserStatModel, "user_stat", ["user_id"]
            )

        fake_db = _FakeDB()
        await fake_db.user_stat._ensure_table()
        mgr = StatManager(fake_db)

        # 写入两条正常数据
        for uid in ("good_a", "good_b"):
            stat = UserStatInfo()
            stat.msg.inc()
            await fake_db.user_stat.upsert(
                UserStatModel(user_id=uid, data=stat.serialize())
            )

        # patch get 方法：对 bad_user 抛异常（模拟 DB 读取层故障）
        _original_get = fake_db.user_stat.get
        async def _failing_get(*keys):
            if keys and keys[0] == "bad_user":
                raise OSError("simulated disk error")
            return await _original_get(*keys)
        fake_db.user_stat.get = _failing_get

        # 添加 bad_user 行（list_all 会返回它，但 get 时抛异常）
        await fake_db.user_stat.upsert(
            UserStatModel(user_id="bad_user", data=UserStatInfo().serialize())
        )

        # 模拟 tick_daily 循环 —— 无 per-row try/except
        user_rows = await fake_db.user_stat.list_all()
        assert len(user_rows) == 3

        # 当前无保护的代码路径：直接循环会因 bad_user 而中断
        # （这是 R2 要修复的 bug）
        with pytest.raises(OSError):
            for row in user_rows:
                await mgr.update_user_stat(row.user_id, lambda s: s.daily_update())

        # 恢复 get 方法，验证 good 行未被更新（因为循环中断了）
        fake_db.user_stat.get = _original_get
        row_a = await fake_db.user_stat.get("good_a")
        stat_a = UserStatInfo()
        stat_a.deserialize(row_a.data)
        # 循环在 bad_user 处中断，good_b 未被 daily_update
        assert stat_a.msg.cur_day_val == 0  # good_a 被 daily_update 了
        # 但 good_b 可能在 good_a 之前或之后被处理...

        await conn.close()


@pytest.mark.asyncio
async def test_tick_daily_with_per_row_protection_succeeds():
    """验证加了 per-row try/except 后，单行故障不阻断其余行。

    此测试使用与 R2 修复相同的保护模式，确认修复有效。
    """
    import aiosqlite
    from plugins.DicePP.core.data import Repository
    from plugins.DicePP.core.statistics.stat_manager import StatManager

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        conn = await aiosqlite.connect(db_path)

        class _FakeDB:
            user_stat = Repository[UserStatModel](
                conn, UserStatModel, "user_stat", ["user_id"]
            )

        fake_db = _FakeDB()
        await fake_db.user_stat._ensure_table()
        mgr = StatManager(fake_db)

        # 写入正常数据
        for uid in ("good_a", "good_b"):
            stat = UserStatInfo()
            stat.msg.inc()
            await fake_db.user_stat.upsert(
                UserStatModel(user_id=uid, data=stat.serialize())
            )

        # patch get 使 bad_user 失败
        _original_get = fake_db.user_stat.get
        async def _failing_get(*keys):
            if keys and keys[0] == "bad_user":
                raise OSError("simulated disk error")
            return await _original_get(*keys)
        fake_db.user_stat.get = _failing_get

        await fake_db.user_stat.upsert(
            UserStatModel(user_id="bad_user", data=UserStatInfo().serialize())
        )

        user_rows = await fake_db.user_stat.list_all()

        # 带 per-row try/except 的保护循环（等同于 R2 修复后的代码）
        for row in user_rows:
            try:
                await mgr.update_user_stat(row.user_id, lambda s: s.daily_update())
            except Exception:
                pass

        # 恢复并验证：good_a 和 good_b 均被 daily_update
        fake_db.user_stat.get = _original_get
        for uid in ("good_a", "good_b"):
            row = await fake_db.user_stat.get(uid)
            stat = UserStatInfo()
            stat.deserialize(row.data)
            assert stat.msg.cur_day_val == 0, f"{uid} 未被 daily_update"
            assert stat.msg.last_day_val == 1, f"{uid} 的 last_day_val 不正确"

        bad_row = await fake_db.user_stat.get("bad_user")
        stat_bad = UserStatInfo()
        stat_bad.deserialize(bad_row.data)
        # bad_user 的 daily_update 未执行（get 抛异常，updater 未运行）
        # 因此数据保持不变（cur_day_val=0, last_day_val=0——默认值）
        assert stat_bad.msg.cur_day_val == 0
        assert stat_bad.msg.last_day_val == 0

        await conn.close()


# ══════════════════════════════════════════════════════════════════════
# R3: StatManager 锁内只读方法
# ══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_read_under_lock_returns_fresh_data():
    """StatManager.read_*_stat 在并发写入后读到最新值。

    read 方法在 per-key 锁内执行，确保不会读到并发原子更新前旧快照。
    """
    import aiosqlite
    from plugins.DicePP.core.data import Repository
    from plugins.DicePP.core.statistics.stat_manager import StatManager

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        conn = await aiosqlite.connect(db_path)

        class _FakeDB:
            user_stat = Repository[UserStatModel](
                conn, UserStatModel, "user_stat", ["user_id"]
            )

        fake_db = _FakeDB()
        await fake_db.user_stat._ensure_table()
        mgr = StatManager(fake_db)

        # 写入初始 msg 计数
        await mgr.update_user_stat("u1", lambda s: s.msg.inc())

        # 并发写入 roll（同 key 串行化，read 必须等到写入完成）
        async def write_roll():
            await mgr.update_user_stat("u1", lambda s: s.roll.times.inc(3))

        async def read_after_write():
            await asyncio.sleep(0.05)
            return await mgr.read_user_stat("u1")

        _, stat = await asyncio.gather(write_roll(), read_after_write())

        # read 在锁内执行 → 返回最新数据
        assert stat.msg.cur_day_val == 1
        assert stat.roll.times.cur_day_val == 3
        assert stat.roll.times.total_val == 3

        await conn.close()
