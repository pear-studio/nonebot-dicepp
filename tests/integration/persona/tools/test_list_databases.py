"""list_query_databases 工具测试。"""

import json

import pytest

from plugins.DicePP.module.persona.agent.runtime_types import ToolExecutionContext
from plugins.DicePP.module.persona.tools.list_databases import (
    LIST_QUERY_DATABASES_TOOL,
    build_list_databases_tool,
)




def _ctx() -> ToolExecutionContext:
    return ToolExecutionContext(
        run_id="r1",
        tool_call_id="tc1",
        call_index=0,
        same_name_index=0,
    )


async def _execute(tool) -> dict | str:
    result = await tool.handler(tool.args_schema(), _ctx())
    try:
        return json.loads(result.observation)
    except json.JSONDecodeError:
        return result.observation


class TestListDatabases:
    @pytest.mark.asyncio
    async def test_list_databases_returns_info(self, query_store):
        store, make_db = query_store
        db_name = await make_db("LISTDBTEST")

        await store.execute(
            db_name,
            "INSERT INTO data VALUES(?,?,?,?,?,?)",
            ("测试条目", "TestEntry", "PHB", "法术", "通用", "内容"),
            commit=True,
        )

        async def resolve_db(user_id, group_id):
            return db_name

        tool = build_list_databases_tool(store, resolve_db, user_id="test_user")
        data = await _execute(tool)

        assert data["default"] == db_name
        found = [d for d in data["databases"] if d["name"] == db_name]
        assert len(found) == 1
        assert found[0]["rows"] == 1
        assert "法术" in found[0]["categories"]

    @pytest.mark.asyncio
    async def test_list_databases_query_unavailable(self):
        async def resolve_db(user_id, group_id):
            return ""

        tool = build_list_databases_tool(None, resolve_db)
        observation = await _execute(tool)

        assert "不可用" in observation

    @pytest.mark.asyncio
    async def test_multiple_databases(self, query_store):
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

        async def resolve_db(user_id, group_id):
            return db_a

        tool = build_list_databases_tool(store, resolve_db)
        data = await _execute(tool)

        assert {d["name"] for d in data["databases"]} == {db_a, db_b}

    @pytest.mark.asyncio
    async def test_resolve_db_exception_sets_default_none(self, query_store):
        store, make_db = query_store
        db_name = await make_db("EXCEPTEST")
        await store.execute(
            db_name,
            "INSERT INTO data VALUES(?,?,?,?,?,?)",
            ("条目", "Entry", "PHB", "法术", "", "内容"),
            commit=True,
        )

        async def resolve_db(user_id, group_id):
            raise RuntimeError("resolve failed")

        tool = build_list_databases_tool(store, resolve_db)
        data = await _execute(tool)

        assert data["default"] is None
        assert len(data["databases"]) == 1

    @pytest.mark.asyncio
    async def test_empty_database_zero_rows(self, query_store):
        store, make_db = query_store
        db_name = await make_db("EMPTYDB")

        async def resolve_db(user_id, group_id):
            return db_name

        tool = build_list_databases_tool(store, resolve_db)
        data = await _execute(tool)

        found = [d for d in data["databases"] if d["name"] == db_name]
        assert len(found) == 1
        assert found[0]["rows"] == 0
        assert found[0]["categories"] == []

    @pytest.mark.asyncio
    async def test_categories_empty_and_filled(self, query_store):
        store, make_db = query_store
        db_name = await make_db("CATEGORYDB")
        await store.execute(
            db_name,
            "INSERT INTO data VALUES(?,?,?,?,?,?)",
            ("无分类", "NoCat", "PHB", "", "通用", "内容"),
            commit=True,
        )
        await store.execute(
            db_name,
            "INSERT INTO data VALUES(?,?,?,?,?,?)",
            ("有分类", "HasCat", "PHB", "魔法", "通用", "内容"),
            commit=True,
        )

        async def resolve_db(user_id, group_id):
            return db_name

        tool = build_list_databases_tool(store, resolve_db)
        data = await _execute(tool)

        found = [d for d in data["databases"] if d["name"] == db_name]
        assert len(found) == 1
        assert found[0]["rows"] == 2
        assert found[0]["categories"] == ["魔法"]

    @pytest.mark.asyncio
    async def test_return_format_stability(self, query_store):
        store, make_db = query_store
        db_name = await make_db("FORMATDB")
        await store.execute(
            db_name,
            "INSERT INTO data VALUES(?,?,?,?,?,?)",
            ("条目", "Entry", "PHB", "战斗", "通用", "内容"),
            commit=True,
        )

        async def resolve_db(user_id, group_id):
            return db_name

        tool = build_list_databases_tool(store, resolve_db)
        data = await _execute(tool)

        assert set(data.keys()) == {"databases", "default"}
        db_info = data["databases"][0]
        assert set(db_info.keys()) == {"name", "rows", "categories"}
        assert db_info["name"] == db_name
        assert isinstance(db_info["rows"], int)
        assert isinstance(db_info["categories"], list)


class TestListDatabasesToolSpec:
    def test_tool_spec_format(self):
        assert LIST_QUERY_DATABASES_TOOL.name == "list_query_databases"
        assert LIST_QUERY_DATABASES_TOOL.description
        schema = LIST_QUERY_DATABASES_TOOL.args_schema.model_json_schema()
        assert schema["properties"] == {}
