"""
评分失败记录存储单元测试
"""

from datetime import datetime, timedelta
import aiosqlite
import pytest

from plugins.DicePP.module.persona.data.store import PersonaDataStore
from plugins.DicePP.module.persona.data.models import ScoringFailure


@pytest.mark.asyncio
async def test_record_scoring_failure_defaults():
    """record_scoring_failure 默认值写入"""
    async with aiosqlite.connect(":memory:") as db:
        store = PersonaDataStore(db)
        await store.ensure_tables()

        await store.record_scoring_failure(
            ScoringFailure(user_id="u1", group_id="g1", messages_count=10, error="timeout")
        )

        failures = await store.get_recent_scoring_failures("u1", "g1")
        assert len(failures) == 1
        f = failures[0]
        assert f.user_id == "u1"
        assert f.group_id == "g1"
        assert f.messages_count == 10
        assert f.error == "timeout"
        assert f.raw_response == ""
        assert f.created_at is not None


@pytest.mark.asyncio
async def test_record_scoring_failure_explicit_fields():
    """record_scoring_failure 显式字段写入"""
    async with aiosqlite.connect(":memory:") as db:
        store = PersonaDataStore(db)
        await store.ensure_tables()

        now = datetime(2026, 5, 8, 12, 0, 0)
        await store.record_scoring_failure(
            ScoringFailure(
                user_id="u2",
                group_id="",
                messages_count=5,
                error="parse_error",
                raw_response="not json",
                created_at=now,
            )
        )

        failures = await store.get_recent_scoring_failures("u2", "")
        assert len(failures) == 1
        f = failures[0]
        assert f.messages_count == 5
        assert f.raw_response == "not json"
        assert f.created_at == now


@pytest.mark.asyncio
async def test_get_recent_scoring_failures_filter_and_order():
    """get_recent_scoring_failures 按 user_id/group_id 过滤、按 created_at DESC 排序"""
    async with aiosqlite.connect(":memory:") as db:
        store = PersonaDataStore(db)
        await store.ensure_tables()

        base = datetime(2026, 5, 8, 12, 0, 0)
        for i in range(3):
            await store.record_scoring_failure(
                ScoringFailure(
                    user_id="u3",
                    group_id="g3",
                    messages_count=i + 1,
                    error=f"err{i}",
                    created_at=base + timedelta(minutes=i),
                )
            )
        # 不同 user
        await store.record_scoring_failure(
            ScoringFailure(user_id="u_other", group_id="g3", messages_count=99, error="other")
        )

        failures = await store.get_recent_scoring_failures("u3", "g3", limit=10)
        assert len(failures) == 3
        # DESC 排序：最新的在前
        assert failures[0].error == "err2"
        assert failures[1].error == "err1"
        assert failures[2].error == "err0"

        # 过滤其他 user
        other = await store.get_recent_scoring_failures("u_other", "g3")
        assert len(other) == 1
        assert other[0].messages_count == 99


@pytest.mark.asyncio
async def test_prune_scoring_failures_by_days():
    """prune_scoring_failures 按天数正确清理（覆盖 R2 同天边界）"""
    async with aiosqlite.connect(":memory:") as db:
        store = PersonaDataStore(db)
        await store.ensure_tables()

        now = datetime(2026, 5, 8, 15, 0, 0)
        # 插入一条旧记录（上午10点，应被清理）
        old = now - timedelta(days=2)
        await store.record_scoring_failure(
            ScoringFailure(
                user_id="u4",
                group_id="g4",
                messages_count=1,
                error="old",
                created_at=old,
            )
        )
        # 插入一条同天但更早的记录（上午11点，仍在 1 天 cutoff 内，不应被清理）
        same_day_early = now - timedelta(hours=4)  # 11:00
        await store.record_scoring_failure(
            ScoringFailure(
                user_id="u4",
                group_id="g4",
                messages_count=2,
                error="same_day",
                created_at=same_day_early,
            )
        )
        # 插入一条新记录
        await store.record_scoring_failure(
            ScoringFailure(
                user_id="u4",
                group_id="g4",
                messages_count=3,
                error="new",
                created_at=now,
            )
        )

        # 清理 1 天前的记录
        deleted = await store.prune_scoring_failures(1)
        assert deleted == 1

        remaining = await store.get_recent_scoring_failures("u4", "g4", limit=10)
        assert len(remaining) == 2
        errors = {r.error for r in remaining}
        assert "old" not in errors
        assert "same_day" in errors
        assert "new" in errors
