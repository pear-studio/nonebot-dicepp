"""QueryStore SQLite and query command integration tests."""
import pytest
import os
import asyncio
import tempfile


# ─────────────────────────── 单元测试：QueryStore.create_empty_database ─────────

class TestCreateEmptySqliteDatabase:
    """QueryStore.create_empty_database 函数测试"""

    def test_creates_valid_db_file(self):
        from core.data.query_store import QueryStore
        store = QueryStore()
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test_query.db")
            result = asyncio.run(store.create_empty_database(db_path))
            assert result is True, "应成功创建数据库"
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
            assert result1 is True, "第一次创建应成功"
            # 第二次调用在 data 表已存在时应抛出异常
            with pytest.raises(Exception):
                asyncio.run(store.create_empty_database(db_path))
        finally:
            import gc
            gc.collect()
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)


# ─────────────────────────── 集成测试：.查询 指令 ───────────────────────────

@pytest.fixture
def _send_group_factory(fresh_bot):
    """Module-level fixture for sending group messages with a shared bot."""
    bot, _proxy = fresh_bot

    async def send_group(msg: str, user_id: str = "user1", group_id: str = "group1",
                         nickname: str = "测试用户"):
        from core.communication import MessageMetaData, MessageSender
        meta = MessageMetaData(msg, msg, MessageSender(user_id, nickname), group_id, False)
        return await bot.process_message(msg, meta)

    return send_group


@pytest.mark.asyncio
class TestQueryCommandIntegration:
    """QueryCommand (.查询/.q) 集成测试"""

    @pytest.mark.parametrize("command", [".查询 火球术", ".q 火球术"])
    async def test_query_no_database_returns_response(self, command, _send_group_factory):
        """没有数据库时 .查询/.q 应返回错误提示而非崩溃"""
        cmds = await _send_group_factory(command)
        assert len(cmds) == 1, f"{command.split()[0]} 应返回一条提示信息"
        result = "\n".join([str(c) for c in cmds])
        assert "未加载的数据库" in result


@pytest.mark.asyncio
class TestHomebrewCommandIntegration:
    """HomebrewCommand (.私设/.hb) 集成测试"""

    async def test_homebrew_status_returns_response(self, _send_group_factory):
        """查询私设状态——应返回私设状态提示而非空列表"""
        cmds = await _send_group_factory(".hb status", user_id="test_master")
        assert len(cmds) == 1, ".hb status 应返回一条提示"
        result = "\n".join([str(c) for c in cmds])
        assert "私设" in result
        assert "未载入" in result

    async def test_homebrew_query_no_data_returns_response(self, _send_group_factory):
        """没有私设数据时查询应返回提示而非崩溃"""
        cmds = await _send_group_factory(".私设 测试条目", user_id="test_master")
        assert len(cmds) == 1, ".私设 应返回一条提示"
        result = "\n".join([str(c) for c in cmds])
        assert "私设" in result
        assert "未载入" in result
