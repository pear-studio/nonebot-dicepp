"""
Tests for Persona cost control and dice tools

Covers:
- Quota system (daily limit, exemptions)
- Roll dice tool
"""
import pytest

from plugins.DicePP.module.persona.data.store import PersonaDataStore


# ── Quota System Tests ───────────────────────────────────────────────────────

class TestQuotaSystem:
    """测试配额系统"""

    @pytest.mark.asyncio
    async def test_daily_usage_tracking(self, tmp_path):
        """测试每日用量追踪"""
        import aiosqlite

        async with aiosqlite.connect(":memory:") as persona_db, \
             aiosqlite.connect(":memory:") as core_db:
            store = PersonaDataStore(":memory:", core_db, timezone="Asia/Shanghai")
            store._persona_db = persona_db
            await store.ensure_tables()

            user_id = "U123"
            date = "2024-01-15"

            # 初始用量为 0
            usage = await store.get_daily_usage(user_id, date)
            assert usage == 0

            # 增加用量
            await store.increment_daily_usage(user_id, date)
            await store.increment_daily_usage(user_id, date)

            usage = await store.get_daily_usage(user_id, date)
            assert usage == 2

    @pytest.mark.asyncio
    async def test_daily_usage_separate_dates(self, tmp_path):
        """测试不同日期的用量分开计算"""
        import aiosqlite

        async with aiosqlite.connect(":memory:") as persona_db, \
             aiosqlite.connect(":memory:") as core_db:
            store = PersonaDataStore(":memory:", core_db, timezone="Asia/Shanghai")
            store._persona_db = persona_db
            await store.ensure_tables()

            user_id = "U123"

            # 昨天的用量
            await store.increment_daily_usage(user_id, "2024-01-14")
            await store.increment_daily_usage(user_id, "2024-01-14")

            # 今天的用量
            await store.increment_daily_usage(user_id, "2024-01-15")

            yesterday_usage = await store.get_daily_usage(user_id, "2024-01-14")
            today_usage = await store.get_daily_usage(user_id, "2024-01-15")

            assert yesterday_usage == 2
            assert today_usage == 1

    @pytest.mark.asyncio
    async def test_daily_usage_different_users(self, tmp_path):
        """测试不同用户的用量分开计算"""
        import aiosqlite

        async with aiosqlite.connect(":memory:") as persona_db, \
             aiosqlite.connect(":memory:") as core_db:
            store = PersonaDataStore(":memory:", core_db, timezone="Asia/Shanghai")
            store._persona_db = persona_db
            await store.ensure_tables()

            date = "2024-01-15"

            await store.increment_daily_usage("U123", date)
            await store.increment_daily_usage("U123", date)
            await store.increment_daily_usage("U456", date)

            usage_u123 = await store.get_daily_usage("U123", date)
            usage_u456 = await store.get_daily_usage("U456", date)

            assert usage_u123 == 2
            assert usage_u456 == 1


# ── Roll Dice Tool Tests ─────────────────────────────────────────────────────

# ── R6: 补充测试覆盖 ──────────────────────────────────────────────────────────

class TestWhitelistMembership:
    """测试白名单成员判定"""

    @pytest.mark.asyncio
    async def test_whitelist_user_exempt_from_quota(self, tmp_path, monkeypatch):
        """测试白名单用户豁免配额"""
        import aiosqlite

        async with aiosqlite.connect(":memory:") as persona_db, \
             aiosqlite.connect(":memory:") as core_db:
            store = PersonaDataStore(":memory:", core_db, timezone="Asia/Shanghai")
            store._persona_db = persona_db
            await store.ensure_tables()

            # 添加用户到白名单
            await store.add_user_to_whitelist("WHITELISTED_USER")

            # 白名单用户应被识别
            assert await store.is_user_whitelisted("WHITELISTED_USER") is True
            # 非白名单用户不应被识别
            assert await store.is_user_whitelisted("REGULAR_USER") is False

    @pytest.mark.asyncio
    async def test_whitelist_group_exempt_from_quota(self, tmp_path):
        """测试白名单群豁免配额"""
        import aiosqlite

        async with aiosqlite.connect(":memory:") as persona_db, \
             aiosqlite.connect(":memory:") as core_db:
            store = PersonaDataStore(":memory:", core_db, timezone="Asia/Shanghai")
            store._persona_db = persona_db
            await store.ensure_tables()

            # 添加群到白名单
            await store.add_group_to_whitelist("WHITELISTED_GROUP")

            # 群在白名单中应豁免
            assert await store.is_group_whitelisted("WHITELISTED_GROUP") is True
            assert await store.is_group_whitelisted("REGULAR_GROUP") is False

