"""QueryStore SQLite and query command integration tests."""
import pytest
import os
import asyncio
import tempfile
from unittest.mock import AsyncMock


# ─────────────────────────── 单元测试：QueryStore.create_empty_database ─────────

class TestCreateEmptySqliteDatabase:
    """QueryStore.create_empty_database 函数测试"""

    def test_creates_valid_db_file(self):
        from plugins.DicePP.core.data.query_store import QueryStore
        store = QueryStore()
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test_query.db")
            result = asyncio.run(store.create_empty_database(db_path))
            assert result is True, "应成功创建数据库"
            assert os.path.exists(db_path), "数据库文件应存在"

    def test_created_db_has_data_table(self):
        """创建的数据库应包含 data 和 redirect 两张表"""
        import sqlite3
        from plugins.DicePP.core.data.query_store import QueryStore
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
        from plugins.DicePP.core.data.query_store import QueryStore, QUERY_DATA_FIELD_LIST
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
        from plugins.DicePP.core.data.query_store import QueryStore
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
        from plugins.DicePP.core.communication import MessageMetaData, MessageSender
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

    async def test_player_query_search_redirect_selection_and_paging_remain_available(
        self, fresh_bot, query_db, _send_group_factory
    ):
        """只读收缩不影响查询、全文搜索、重定向解析、选择和翻页。"""
        bot, _proxy = fresh_bot
        db_name = await query_db("READONLYPLAYER")
        bot.config.mode.default = db_name

        for index in range(31):
            await bot.db.query.execute(
                db_name,
                "INSERT INTO data VALUES(?,?,?,?,?,?)",
                (
                    f"条目{index:02d}",
                    f"Entry {index:02d}",
                    "PHB",
                    "测试分类",
                    "通用",
                    f"第{index:02d}条的独特正文",
                ),
                commit=True,
            )
        await bot.db.query.execute(
            db_name,
            "INSERT INTO redirect VALUES(?,?)",
            ("别名条目", "条目00"),
            commit=True,
        )

        redirected = "\n".join(str(c) for c in await _send_group_factory(".查询 别名条目"))
        assert "条目00" in redirected
        assert "重定向自：别名条目" in redirected

        searched = "\n".join(str(c) for c in await _send_group_factory(".搜索 第00条的独特正文"))
        assert "条目00" in searched
        assert "第00条的独特正文" in searched

        candidates = "\n".join(str(c) for c in await _send_group_factory(".查询 条目"))
        assert "0.条目00" in candidates
        assert "1/2页" in candidates

        selected = "\n".join(str(c) for c in await _send_group_factory("1"))
        assert "条目01" in selected
        assert "第01条的独特正文" in selected

        await _send_group_factory(".查询 条目")
        next_page = "\n".join(str(c) for c in await _send_group_factory("+"))
        assert "30.条目30" in next_page
        assert "2/2页" in next_page

    @pytest.mark.parametrize("argument", ["#PHB", "&法术", "/"])
    async def test_category_and_tag_syntax_returns_normal_format_error(
        self, argument, fresh_bot, query_db, _send_group_factory
    ):
        bot, _proxy = fresh_bot
        db_name = await query_db("FORMATERROR")
        bot.config.mode.default = db_name

        result = "\n".join(
            str(command)
            for command in await _send_group_factory(f".查询 {argument}")
        )

        assert "查询格式错误" in result
        assert "语法已停用" not in result

    async def test_outdated_embedded_query_points_to_dashboard_normalization(
        self, fresh_bot, query_db, _send_group_factory
    ):
        bot, _proxy = fresh_bot
        db_name = await query_db("OUTDATED")
        bot.config.mode.default = db_name
        await bot.db.query.execute(
            db_name,
            "INSERT INTO data VALUES(?,?,?,?,?,?)",
            ("汇总", "", "TEST", "", "", "正文\n/火球术"),
            commit=True,
        )

        result = "\n".join(
            str(command) for command in await _send_group_factory(".查询 汇总")
        )

        assert f"数据库“{db_name}”" in result
        assert "包含过时的查询逻辑" in result
        assert "Dashboard 中规范" in result

    async def test_disabling_database_invalidates_an_existing_selection(
        self, fresh_bot, query_db, _send_group_factory, tmp_path
    ):
        from dicepp_data import set_query_database_enabled

        bot, _proxy = fresh_bot
        db_name = await query_db("DISABLESELECTION")
        bot.config.mode.default = db_name
        for index in range(2):
            await bot.db.query.execute(
                db_name,
                "INSERT INTO data VALUES(?,?,?,?,?,?)",
                (f"条目{index}", "", "TEST", "", "", f"内容{index}"),
                commit=True,
            )
        candidates = "\n".join(
            str(command) for command in await _send_group_factory(".查询 条目")
        )
        assert "0.条目0" in candidates

        database_path = tmp_path / f"{db_name}.db"
        set_query_database_enabled(database_path.parent, db_name, False)
        selected = "\n".join(
            str(command) for command in await _send_group_factory("0")
        )

        assert "当前查询数据库未启用" in selected

    async def test_mode_bound_to_disabled_database_is_not_selectable(
        self, fresh_bot, query_db, _send_group_factory, tmp_path
    ):
        from dicepp_data import set_query_database_enabled

        bot, _proxy = fresh_bot
        db_name = await query_db("DISABLEDMODE")
        mode_command = bot.command_dict["ModeCommand"]
        mode_command.mode_dict["DisabledMode"] = ["20", db_name]
        mode_command.mode_upper_map = {
            name.upper(): name for name in mode_command.mode_dict
        }
        set_query_database_enabled(tmp_path, db_name, False)

        result = "\n".join(
            str(command)
            for command in await _send_group_factory(".mode DisabledMode")
        )

        assert "该模式不存在" in result
        assert "已切换至DisabledMode" not in result
        assert "DisabledMode" not in result.split("以下是可用的模式列表：", 1)[-1]

    @pytest.mark.parametrize(
        ("command", "entry_name"),
        [
            (".查询 编辑器", "编辑器"),
            (".查询 创建角色", "创建角色"),
            (".query editor", "editor"),
            (".q create water", "create water"),
        ],
    )
    async def test_management_keyword_prefixes_remain_normal_query_terms(
        self, command, entry_name, fresh_bot, query_db, _send_group_factory
    ):
        """管理关键字只在精确旧语法中 tombstone，词条前缀仍可查询。"""
        bot, _proxy = fresh_bot
        db_name = await query_db("MANAGEMENTPREFIX")
        bot.config.mode.default = db_name
        await bot.db.query.execute(
            db_name,
            "INSERT INTO data VALUES(?,?,?,?,?,?)",
            (entry_name, "", "TEST", "回归", "", f"{entry_name} 的查询内容"),
            commit=True,
        )

        result = "\n".join(str(c) for c in await _send_group_factory(command))
        assert f"{entry_name} 的查询内容" in result
        assert "Bot 端资料管理已停用" not in result

    @pytest.mark.parametrize(
        "command",
        [
            ".查询 创建 危险条目",
            ".query edit 条目00",
            ".搜索 编辑 条目00",
            ".重定向创建 别名=条目00",
            ".redirect delete 别名",
            ".数据库创建 DANGER",
            ".database load DANGER",
            ".数据库卸载 READONLYADMIN",
            ".database import READONLYADMIN 0 data.xlsx",
            ".数据库列表",
        ],
    )
    async def test_legacy_management_commands_are_read_only(
        self, command, fresh_bot, query_db, _send_group_factory, monkeypatch
    ):
        """旧管理命令应明确拒绝，且不调用 QueryStore 的写入/连接管理 API。"""
        bot, _proxy = fresh_bot
        db_name = await query_db("READONLYADMIN")
        bot.config.mode.default = db_name
        await bot.db.query.execute(
            db_name,
            "INSERT INTO data VALUES(?,?,?,?,?,?)",
            ("条目00", "Entry 00", "PHB", "测试", "通用", "原始内容"),
            commit=True,
        )
        await bot.db.query.execute(
            db_name,
            "INSERT INTO redirect VALUES(?,?)",
            ("别名", "条目00"),
            commit=True,
        )
        before_data = await bot.db.query.fetchall(db_name, "SELECT * FROM data")
        before_redirect = await bot.db.query.fetchall(db_name, "SELECT * FROM redirect")

        blocked_methods = {}
        for name in (
            "execute",
            "executemany",
            "connect_path",
            "disconnect_database",
            "create_empty_database",
            "load_data_from_xlsx_to_sqlite",
        ):
            blocked_methods[name] = AsyncMock(side_effect=AssertionError(f"{name} must not be called"))
            monkeypatch.setattr(bot.db.query, name, blocked_methods[name])

        result = "\n".join(
            str(c) for c in await _send_group_factory(command, user_id="test_master")
        )
        assert "Bot 端资料管理已停用" in result
        assert "只读查询" in result
        assert await bot.db.query.fetchall(db_name, "SELECT * FROM data") == before_data
        assert await bot.db.query.fetchall(db_name, "SELECT * FROM redirect") == before_redirect
        for method in blocked_methods.values():
            method.assert_not_awaited()


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
