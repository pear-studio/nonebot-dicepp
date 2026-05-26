"""data_api.py 测试 — 表名白名单 + 路径穿越防护 + CRUD"""
import sqlite3

import pytest


class TestSafeTableName:
    """`_safe_table_name` 是关键 SQL 注入防线，必须严格"""

    def test_accepts_alnum_underscore(self):
        from dicepp_admin.data_api import _safe_table_name
        assert _safe_table_name("group_config") is True
        assert _safe_table_name("characters_dnd") is True
        assert _safe_table_name("table123") is True
        assert _safe_table_name("a") is True

    @pytest.mark.parametrize("bad", [
        "",                  # 空
        "table-name",        # 连字符
        "table name",        # 空格
        "table; DROP",       # 注入意图
        "table'or'1",        # 单引号
        '"injected"',        # 双引号
        "tbl/path",          # 路径分隔
        "table\\name",       # 反斜杠
        "table.name",        # 点
        "table\nname",       # 换行
        ";",                 # 单个 SQL 字符
    ])
    def test_rejects_dangerous(self, bad):
        from dicepp_admin.data_api import _safe_table_name
        assert _safe_table_name(bad) is False, f"应该拒绝 {bad!r}"

    def test_get_table_columns_rejects_bad_name_directly(self):
        """pear S1 加固：_get_table_columns 内嵌白名单，跨调用方都安全"""
        from dicepp_admin.data_api import _get_table_columns
        conn = sqlite3.connect(":memory:")
        # 即便连接已开，非法表名也立即拒，不会到达 PRAGMA
        cols = _get_table_columns(conn, "table; DROP")
        assert cols == []


class TestDeckFilePathTraversal:
    """牌堆/随机文件 CRUD 必须防路径穿越"""

    @pytest.mark.parametrize("evil", [
        "../etc/passwd",
        "..\\..\\windows\\system32",
        "/absolute/path.txt",
        "subdir/../escape.txt",
    ])
    def test_read_deck_rejects_path_traversal(self, tmp_admin_paths, evil):
        from dicepp_admin import data_api
        from pathlib import Path
        # 准备一个合法的牌堆目录（避免触发其他错误）
        content_dir = data_api.AdminPaths.PROJECT_ROOT / "content" / "decks"
        content_dir.mkdir(parents=True, exist_ok=True)
        result = data_api.read_deck_file("any-instance", evil)
        assert result is None, f"应该拒绝 {evil!r}"

    def test_write_deck_rejects_path_traversal(self, tmp_admin_paths):
        from dicepp_admin import data_api
        ok = data_api.write_deck_file("inst", "../escape.txt", "hello")
        assert ok is False

    def test_delete_deck_rejects_path_traversal(self, tmp_admin_paths):
        from dicepp_admin import data_api
        ok = data_api.delete_deck_file("inst", "../boom.txt")
        assert ok is False


class TestNormalFileOps:
    def test_write_then_read_deck(self, tmp_admin_paths):
        from dicepp_admin import data_api
        ok = data_api.write_deck_file("inst1", "test.txt", "deck content")
        assert ok is True
        content = data_api.read_deck_file("inst1", "test.txt")
        assert content == "deck content"

    def test_list_decks(self, tmp_admin_paths):
        from dicepp_admin import data_api
        data_api.write_deck_file("inst1", "a.txt", "x")
        data_api.write_deck_file("inst1", "b.txt", "y")
        files = data_api.list_deck_files("inst1")
        names = [f["name"] for f in files]
        assert "a.txt" in names
        assert "b.txt" in names

    def test_delete_deck(self, tmp_admin_paths):
        from dicepp_admin import data_api
        data_api.write_deck_file("inst1", "tmp.txt", "x")
        assert data_api.delete_deck_file("inst1", "tmp.txt") is True
        assert data_api.read_deck_file("inst1", "tmp.txt") is None


class TestTableListInjectionDefense:
    """list_records 接受用户输入的表名，必须用 _safe_table_name 兜底"""

    def test_list_records_rejects_unsafe_table(self, tmp_admin_paths):
        from dicepp_admin import data_api
        result = data_api.list_records("inst", "bot", "table; DROP")
        # 应该返回 empty 而非抛 SQL 错误
        assert result["records"] == []
        assert result["total"] == 0

    def test_delete_record_rejects_unsafe_table(self, tmp_admin_paths):
        from dicepp_admin import data_api
        n = data_api.delete_record("inst", "bot", "evil; DROP", {"k": "v"})
        assert n == 0

    def test_update_record_rejects_unsafe_table(self, tmp_admin_paths):
        from dicepp_admin import data_api
        ok = data_api.update_record_data("inst", "bot", "evil; DROP",
                                          {"k": "v"}, {"foo": "bar"})
        assert ok is False
