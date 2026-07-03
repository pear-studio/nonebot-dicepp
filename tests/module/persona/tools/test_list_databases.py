"""list_query_databases 工具 — 单元测试

覆盖多个数据库、resolve_db 异常、空数据库、categories 为空/缺失等场景。
"""

import pytest

pytestmark = pytest.mark.unit

import json

from module.persona.tools.list_databases import (
    LIST_QUERY_DATABASES_TOOL,
    list_query_databases_executor,
)
from module.persona.tools.context import ToolContext


class TestListDatabases:
    """list_query_databases executor 测试"""

    @pytest.mark.asyncio
    async def test_list_databases_returns_info(self, query_store):
        """list_query_databases — 返回数据库列表和默认库"""
        store, make_db = query_store
        db_name = await make_db("LISTDBTEST")

        await store.execute(
            db_name,
            "INSERT INTO data VALUES(?,?,?,?,?,?)",
            ("测试条目", "TestEntry", "PHB", "法术", "通用", "内容"),
            commit=True,
        )

        async def _resolve_db(user_id, group_id):
            return db_name

        ctx = ToolContext(
            user_id="test_user", group_id="",
            query=store, resolve_db=_resolve_db,
        )
        result = await list_query_databases_executor({}, ctx)
        data = json.loads(result)

        assert "databases" in data
        assert data["default"] == db_name
        found = [d for d in data["databases"] if d["name"] == db_name]
        assert len(found) == 1
        assert found[0]["rows"] == 1
        assert "法术" in found[0]["categories"]

    @pytest.mark.asyncio
    async def test_list_databases_query_unavailable(self):
        """list_query_databases — query 不可用时的降级"""
        ctx = ToolContext(user_id="test_user", group_id="")
        result = await list_query_databases_executor({}, ctx)
        assert "不可用" in result

    @pytest.mark.asyncio
    async def test_multiple_databases(self, query_store):
        """多个数据库时应全部列出"""
        store, make_db = query_store
        db_a = await make_db("DB_A")
        db_b = await make_db("DB_B")

        for db in (db_a, db_b):
            await store.execute(
                db,
                "INSERT INTO data VALUES(?,?,?,?,?,?)",
                ("条目", "Entry", "PHB", "法术", "", "内容"),
                commit=True,
            )

        async def _resolve_db(user_id, group_id):
            return db_a

        ctx = ToolContext(
            user_id="test_user", group_id="",
            query=store, resolve_db=_resolve_db,
        )
        result = await list_query_databases_executor({}, ctx)
        data = json.loads(result)

        names = {d["name"] for d in data["databases"]}
        assert names == {db_a, db_b}

    @pytest.mark.asyncio
    async def test_resolve_db_exception_sets_default_none(self, query_store):
        """resolve_db 抛出异常时 default 应为 None"""
        store, make_db = query_store
        db_name = await make_db("EXCEPTEST")
        await store.execute(
            db_name,
            "INSERT INTO data VALUES(?,?,?,?,?,?)",
            ("条目", "Entry", "PHB", "法术", "", "内容"),
            commit=True,
        )

        async def _resolve_db(user_id, group_id):
            raise RuntimeError("resolve failed")

        ctx = ToolContext(
            user_id="test_user", group_id="",
            query=store, resolve_db=_resolve_db,
        )
        result = await list_query_databases_executor({}, ctx)
        data = json.loads(result)
        assert data["default"] is None
        # 数据库列表仍正常返回
        assert len(data["databases"]) == 1

    @pytest.mark.asyncio
    async def test_empty_database_zero_rows(self, query_store):
        """空数据库（无数据行）应返回 rows=0, categories=[]"""
        store, make_db = query_store
        db_name = await make_db("EMPTYDB")

        async def _resolve_db(user_id, group_id):
            return db_name

        ctx = ToolContext(
            user_id="test_user", group_id="",
            query=store, resolve_db=_resolve_db,
        )
        result = await list_query_databases_executor({}, ctx)
        data = json.loads(result)

        found = [d for d in data["databases"] if d["name"] == db_name]
        assert len(found) == 1
        assert found[0]["rows"] == 0
        assert found[0]["categories"] == []

    @pytest.mark.asyncio
    async def test_categories_empty_and_filled(self, query_store):
        """分类字段为空和正常填充的混合"""
        store, make_db = query_store
        db_name = await make_db("CATEGORYDB")
        # 插入一条无分类的条目
        await store.execute(
            db_name,
            "INSERT INTO data VALUES(?,?,?,?,?,?)",
            ("无分类", "NoCat", "PHB", "", "通用", "内容"),
            commit=True,
        )
        # 插入一条有分类的条目
        await store.execute(
            db_name,
            "INSERT INTO data VALUES(?,?,?,?,?,?)",
            ("有分类", "HasCat", "PHB", "魔法", "通用", "内容"),
            commit=True,
        )

        async def _resolve_db(user_id, group_id):
            return db_name

        ctx = ToolContext(
            user_id="test_user", group_id="",
            query=store, resolve_db=_resolve_db,
        )
        result = await list_query_databases_executor({}, ctx)
        data = json.loads(result)

        found = [d for d in data["databases"] if d["name"] == db_name]
        assert len(found) == 1
        assert found[0]["rows"] == 2
        # 分类应只含非空值
        assert found[0]["categories"] == ["魔法"]

    @pytest.mark.asyncio
    async def test_return_format_stability(self, query_store):
        """返回格式的稳定性断言"""
        store, make_db = query_store
        db_name = await make_db("FORMATDB")
        await store.execute(
            db_name,
            "INSERT INTO data VALUES(?,?,?,?,?,?)",
            ("条目", "Entry", "PHB", "战斗", "通用", "内容"),
            commit=True,
        )

        async def _resolve_db(user_id, group_id):
            return db_name

        ctx = ToolContext(
            user_id="test_user", group_id="",
            query=store, resolve_db=_resolve_db,
        )
        result = await list_query_databases_executor({}, ctx)
        data = json.loads(result)

        # 顶层字段
        assert set(data.keys()) == {"databases", "default"}
        # database 条目字段
        assert len(data["databases"]) == 1
        db_info = data["databases"][0]
        assert set(db_info.keys()) == {"name", "rows", "categories"}
        assert db_info["name"] == db_name
        assert isinstance(db_info["rows"], int)
        assert isinstance(db_info["categories"], list)


class TestListDatabasesToolDef:
    """LIST_QUERY_DATABASES_TOOL ToolDef 格式测试"""

    def test_tool_def_format(self):
        """ToolDef 符合基础格式"""
        d = LIST_QUERY_DATABASES_TOOL.to_openai_format()
        assert d["type"] == "function"
        assert d["function"]["name"] == "list_query_databases"
        assert "description" in d["function"]
        assert d["function"]["parameters"] == {"type": "object", "properties": {}, "required": []}
