"""
契约测试: PersonaDataStore — 数据持久化层

覆盖:
- ensure_tables 幂等性
- 设置型 CRUD (get/set/delete)
- 事务回滚 (story_deck upsert 的 BEGIN IMMEDIATE)
"""
import pytest
import aiosqlite

from plugins.DicePP.module.persona.data.store import PersonaDataStore


class TestStoreEnsureTables:
    """Q35: ensure_tables 幂等性"""

    @pytest.mark.asyncio
    async def test_ensure_tables_idempotent(self):
        """第一次调用 ensure_tables 正常完成，第二次调用不报错"""
        async with aiosqlite.connect(":memory:") as persona_db, \
             aiosqlite.connect(":memory:") as core_db:
            store = PersonaDataStore(":memory:", core_db, timezone="Asia/Shanghai")
            store._persona_db = persona_db
            await store.ensure_tables()
            await store.ensure_tables()


class TestStoreSettingCRUD:
    """Q35: 设置型 CRUD 基本操作"""

    @pytest.mark.asyncio
    async def test_setting_crud(self):
        """get_setting / set_setting / delete_setting 正常读写"""
        async with aiosqlite.connect(":memory:") as persona_db, \
             aiosqlite.connect(":memory:") as core_db:
            store = PersonaDataStore(":memory:", core_db, timezone="Asia/Shanghai")
            store._persona_db = persona_db
            await store.ensure_tables()

            # 初始为空
            val = await store.get_setting("test_key")
            assert val is None

            # set
            await store.set_setting("test_key", "hello")
            val = await store.get_setting("test_key")
            assert val == "hello"

            # overwrite
            await store.set_setting("test_key", "world")
            val = await store.get_setting("test_key")
            assert val == "world"

            # delete
            await store.delete_setting("test_key")
            val = await store.get_setting("test_key")
            assert val is None


class TestStoreTransactionRollback:
    """Q35: 事务回滚 — 失败时不该有部分写入"""

    @pytest.mark.asyncio
    async def test_transaction_rollback_on_failure(self):
        """upsert_story_deck_entry 在 INSERT 失败时 rollback，不留下脏数据"""
        async with aiosqlite.connect(":memory:") as persona_db, \
             aiosqlite.connect(":memory:") as core_db:
            store = PersonaDataStore(":memory:", core_db, timezone="Asia/Shanghai")
            store._persona_db = persona_db
            await store.ensure_tables()

            # 插入一条合法条目
            ok, err = await store.upsert_story_deck_entry("老李", "entity", "图书管理员")
            assert ok is True
            assert err is None

            # 尝试插入一条 content 超长 (超过 300 字) 的条目 — 应在写入前校验失败
            long_content = "a" * 301
            ok, err = await store.upsert_story_deck_entry("老王", "entity", long_content)
            assert ok is False
            assert "超长" in (err or "")

            # 验证数据库状态不变：只有老李
            count = await store.get_story_deck_count()
            assert count == 1
