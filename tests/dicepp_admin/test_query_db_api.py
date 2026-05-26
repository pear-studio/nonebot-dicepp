"""query_db_api.py 测试 — 路径校验 + 表名校验 + CRUD"""
import pytest


class TestSafeDbPath:
    @pytest.mark.parametrize("bad", [
        "",                  # 空
        "../escape.db",      # 路径穿越
        "subdir/../x.db",
        "/abs/path.db",
        "file.txt",          # 非 .db
        "/tmp/data.db",
        "name.exe",
    ])
    def test_rejects_bad_names(self, tmp_admin_paths, bad):
        from dicepp_admin.query_db_api import _safe_db_path
        assert _safe_db_path(bad) is None, f"应该拒绝 {bad!r}"

    @pytest.mark.parametrize("good", [
        "DND5E2024.db",
        "homebrew.db",
        "a.db",
        "test_123.db",
    ])
    def test_accepts_good_names(self, tmp_admin_paths, good):
        from dicepp_admin.query_db_api import _safe_db_path
        result = _safe_db_path(good)
        assert result is not None, f"应该接受 {good!r}"
        assert result.name == good


class TestCreateAndDelete:
    def test_create_database_creates_schema(self, tmp_admin_paths):
        from dicepp_admin import query_db_api as q
        result = q.create_database("new_test.db")
        assert result["ok"] is True
        # 表已建好
        entries = q.list_entries("new_test.db", table="data")
        assert entries["total"] == 0
        assert "名称" in entries["fields"]

    def test_create_duplicate_returns_error(self, tmp_admin_paths):
        from dicepp_admin import query_db_api as q
        q.create_database("dup.db")
        result = q.create_database("dup.db")
        assert result["ok"] is False

    def test_create_with_bad_name(self, tmp_admin_paths):
        from dicepp_admin import query_db_api as q
        result = q.create_database("../escape.db")
        assert result["ok"] is False

    def test_delete_database(self, tmp_admin_paths):
        from dicepp_admin import query_db_api as q
        q.create_database("doomed.db")
        result = q.delete_database("doomed.db")
        assert result["ok"] is True
        # 再删返回 not exist
        result2 = q.delete_database("doomed.db")
        assert result2["ok"] is False


class TestEntryCRUD:
    def test_upsert_insert_then_update(self, tmp_admin_paths):
        from dicepp_admin import query_db_api as q
        q.create_database("crud.db")
        # 插入
        ins = q.upsert_entry("crud.db", "data", None, {
            "名称": "火球术", "英文": "Fireball", "来源": "PHB",
            "分类": "法术", "标签": "AoE/3环", "内容": "20英尺 8d6"
        })
        assert ins["ok"] is True
        assert ins["rowid"] >= 1

        # 更新
        upd = q.upsert_entry("crud.db", "data", ins["rowid"], {
            "名称": "火球术", "英文": "Fireball", "来源": "PHB",
            "分类": "法术", "标签": "AoE/3环", "内容": "30英尺 10d6（私设）"
        })
        assert upd["ok"] is True
        assert upd["updated"] >= 1

        # 查询验证
        rows = q.list_entries("crud.db", table="data")
        assert rows["total"] == 1
        assert "10d6" in rows["entries"][0]["内容"]

    def test_delete_entry(self, tmp_admin_paths):
        from dicepp_admin import query_db_api as q
        q.create_database("del.db")
        r = q.upsert_entry("del.db", "data", None, {"名称": "x"})
        d = q.delete_entry("del.db", "data", r["rowid"])
        assert d["ok"] is True
        assert d["deleted"] >= 1

    def test_rejects_bad_table(self, tmp_admin_paths):
        from dicepp_admin import query_db_api as q
        q.create_database("t.db")
        ins = q.upsert_entry("t.db", "evil; DROP", None, {"名称": "x"})
        assert ins["ok"] is False


class TestListWithFilters:
    def test_keyword_filter(self, tmp_admin_paths):
        from dicepp_admin import query_db_api as q
        q.create_database("kw.db")
        for name in ["火球术", "冰锥术", "光弹"]:
            q.upsert_entry("kw.db", "data", None, {"名称": name, "分类": "法术"})
        r = q.list_entries("kw.db", table="data", keyword="火球")
        names = [e["名称"] for e in r["entries"]]
        assert "火球术" in names
        assert "冰锥术" not in names

    def test_distinct_values(self, tmp_admin_paths):
        from dicepp_admin import query_db_api as q
        q.create_database("d.db")
        q.upsert_entry("d.db", "data", None, {"名称": "a", "分类": "法术"})
        q.upsert_entry("d.db", "data", None, {"名称": "b", "分类": "怪物"})
        q.upsert_entry("d.db", "data", None, {"名称": "c", "分类": "法术"})
        cats = q.get_distinct_values("d.db", "分类", table="data")
        assert "法术" in cats
        assert "怪物" in cats
        assert len([c for c in cats if c == "法术"]) == 1   # distinct

    def test_distinct_rejects_bad_field(self, tmp_admin_paths):
        from dicepp_admin import query_db_api as q
        q.create_database("d.db")
        result = q.get_distinct_values("d.db", "not_a_real_field")
        assert result == []
