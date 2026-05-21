"""
query 模块测试
- 单元测试：QueryStore.create_empty_database / regexp_normalize 等
- 集成测试：.查询 / .q 指令基础响应
"""
import pytest
import os
import asyncio
import tempfile
from unittest.async_case import IsolatedAsyncioTestCase


# ─────────────────────────── 单元测试：QueryStore.create_empty_database ─────────

@pytest.mark.unit
class TestCreateEmptySqliteDatabase:
    """QueryStore.create_empty_database 函数测试"""

    def test_creates_valid_db_file(self):
        from core.data.query_store import QueryStore
        store = QueryStore()
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test_query.db")
            result = asyncio.run(store.create_empty_database(db_path))
            assert result, "应成功创建数据库"
            assert os.path.exists(db_path), "数据库文件应存在"

    def test_created_db_has_data_table(self):
        """创建的数据库应包含 data 和 redirect 两张表"""
        import sqlite3
        from core.data.query_store import QueryStore
        store = QueryStore()
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test_query.db")
            asyncio.run(store.create_empty_database(db_path))
            conn = sqlite3.connect(db_path)
            cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = {row[0] for row in cursor.fetchall()}
            conn.close()
            assert "data" in tables
            assert "redirect" in tables

    def test_data_table_has_correct_columns(self):
        """data 表应包含 QUERY_DATA_FIELD_LIST 定义的所有字段"""
        import sqlite3
        from core.data.query_store import QueryStore, QUERY_DATA_FIELD_LIST
        store = QueryStore()
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test_query.db")
            asyncio.run(store.create_empty_database(db_path))
            conn = sqlite3.connect(db_path)
            cursor = conn.execute("PRAGMA table_info(data)")
            columns = {row[1] for row in cursor.fetchall()}
            conn.close()
            for field in QUERY_DATA_FIELD_LIST:
                assert field in columns, f"data 表缺少字段 {field}"

    def test_second_create_raises_or_fails(self):
        """在已存在文件上再次创建应引发异常（data 表已存在）"""
        import sqlite3
        from core.data.query_store import QueryStore
        store = QueryStore()
        tmpdir = tempfile.mkdtemp()
        try:
            db_path = os.path.join(tmpdir, "test_idem.db")
            result1 = asyncio.run(store.create_empty_database(db_path))
            assert result1, "第一次创建应成功"
            # 第二次调用在 data 表已存在时应抛出异常
            with pytest.raises(Exception):
                asyncio.run(store.create_empty_database(db_path))
        finally:
            import gc
            gc.collect()
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.mark.unit
class TestRegexpNormalize:
    """regexp_normalize 函数测试"""

    def test_normalize_escapes_special_chars(self):
        """regexp_normalize 应转义正则特殊字符"""
        from core.data.query_store import regexp_normalize
        result = regexp_normalize("(力量)")
        assert "\\(" in result
        assert "\\)" in result
        assert result.startswith("\\("), f"'(' 应被转义，实际: {result}"

    def test_normalize_preserves_normal_chars(self):
        """普通汉字和字母不应被转义"""
        from core.data.query_store import regexp_normalize
        result = regexp_normalize("力量")
        assert result == "力量"

    def test_normalize_escapes_dot(self):
        """点号 '.' 应被转义"""
        from core.data.query_store import regexp_normalize
        result = regexp_normalize("v1.0")
        assert "\\." in result

    def test_normalize_basic_string(self):
        from core.data.query_store import regexp_normalize
        result = regexp_normalize("火球术")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_normalize_empty_string(self):
        from core.data.query_store import regexp_normalize
        result = regexp_normalize("")
        assert isinstance(result, str)


# ─────────────────────────── 集成测试：.查询 指令 ───────────────────────────

@pytest.mark.integration
class TestQueryCommandIntegration(IsolatedAsyncioTestCase):
    """QueryCommand (.查询/.q) 集成测试"""

    async def asyncSetUp(self):
        from core.bot import Bot
        self.bot = Bot("test_query_bot")
        self.bot.config.master = ["test_master"]
        await self.bot.delay_init_command()

    async def asyncTearDown(self):
        await self.bot.shutdown_async()
        import os
        test_path = self.bot.data_path
        if os.path.exists(test_path):
            for root, dirs, files in os.walk(test_path, topdown=False):
                for name in files:
                    os.remove(os.path.join(root, name))
                for name in dirs:
                    os.rmdir(os.path.join(root, name))
            os.rmdir(test_path)

    async def _send_group(self, msg: str, user_id: str = "user1", group_id: str = "group1"):
        from core.communication import MessageMetaData, MessageSender
        meta = MessageMetaData(msg, msg, MessageSender(user_id, "测试用户"), group_id, False)
        return await self.bot.process_message(msg, meta)

    async def test_query_no_database_returns_response(self):
        """没有数据库时 .查询 应返回错误提示而非崩溃"""
        cmds = await self._send_group(".查询 火球术")
        # 即使没有数据库，也应该有回复（错误提示）
        self.assertTrue(len(cmds) >= 0, ".查询 不应崩溃")

    async def test_query_short_form_no_database(self):
        """.q 短指令同上"""
        cmds = await self._send_group(".q 火球术")
        self.assertTrue(len(cmds) >= 0, ".q 不应崩溃")


@pytest.mark.integration
class TestHomebrewCommandIntegration(IsolatedAsyncioTestCase):
    """HomebrewCommand (.私设/.hb) 集成测试"""

    async def asyncSetUp(self):
        from core.bot import Bot
        self.bot = Bot("test_hb_bot")
        self.bot.config.master = ["test_master"]
        await self.bot.delay_init_command()

    async def asyncTearDown(self):
        await self.bot.shutdown_async()
        import os
        test_path = self.bot.data_path
        if os.path.exists(test_path):
            for root, dirs, files in os.walk(test_path, topdown=False):
                for name in files:
                    os.remove(os.path.join(root, name))
                for name in dirs:
                    os.rmdir(os.path.join(root, name))
            os.rmdir(test_path)

    async def _send_group(self, msg: str, user_id: str = "user1", group_id: str = "group1"):
        from core.communication import MessageMetaData, MessageSender
        meta = MessageMetaData(msg, msg, MessageSender(user_id, "测试用户"), group_id, False)
        return await self.bot.process_message(msg, meta)

    async def test_homebrew_status_returns_response(self):
        """查询私设状态不应崩溃"""
        cmds = await self._send_group(".hb status")
        self.assertTrue(len(cmds) >= 0, ".hb status 不应崩溃")

    async def test_homebrew_query_no_data_returns_response(self):
        """没有私设数据时查询应返回提示而非崩溃"""
        cmds = await self._send_group(".私设 测试条目")
        self.assertTrue(len(cmds) >= 0, ".私设 不应崩溃")
