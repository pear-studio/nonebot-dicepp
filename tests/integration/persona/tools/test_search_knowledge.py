"""search_knowledge 工具测试。"""

import json

import pytest

from plugins.DicePP.module.persona.agent.runtime_types import ToolExecutionContext
from plugins.DicePP.module.persona.tools.search_knowledge import (
    SEARCH_KNOWLEDGE_TOOL,
    build_search_knowledge_tool,
)




def _ctx() -> ToolExecutionContext:
    return ToolExecutionContext(
        run_id="r1",
        tool_call_id="tc1",
        call_index=0,
        same_name_index=0,
    )


def _tool(store, db_name: str = ""):
    async def resolve_db(user_id, group_id):
        return db_name

    return build_search_knowledge_tool(store, resolve_db, user_id="test_user")


async def _execute(tool, **kwargs) -> dict | str:
    result = await tool.handler(tool.args_schema(**kwargs), _ctx())
    try:
        return json.loads(result.observation)
    except json.JSONDecodeError:
        return result.observation


@pytest.mark.asyncio
async def test_search_knowledge_summary_mode(query_store):
    store, make_db = query_store
    db_name = await make_db("SQSTEST")

    long_content = "这是一个非常长的内容" * 20
    await store.execute(
        db_name,
        "INSERT INTO data VALUES(?,?,?,?,?,?)",
        ("火球术", "Fireball", "PHB", "法术", "塑能 3环", long_content),
        commit=True,
    )

    data = await _execute(_tool(store, db_name), keyword="火球术")

    assert data["total"] == 1
    item = data["results"][0]
    assert item["name"] == "火球术"
    assert item["source"] == "PHB"
    assert "catalogue" not in item
    assert len(item["snippet"]) <= 153
    assert "content" not in item


@pytest.mark.asyncio
async def test_search_knowledge_detail_mode(query_store):
    store, make_db = query_store
    db_name = await make_db("SQDETAIL")

    await store.execute(
        db_name,
        "INSERT INTO data VALUES(?,?,?,?,?,?)",
        ("火球术", "Fireball", "PHB", "法术", "塑能 3环", "完整火球术描述内容"),
        commit=True,
    )

    data = await _execute(_tool(store, db_name), keyword="火球术", detail_index=0)

    assert data["name"] == "火球术"
    assert data["content"] == "完整火球术描述内容"
    assert "snippet" not in data


@pytest.mark.asyncio
async def test_search_knowledge_database_not_found(query_store):
    store, _ = query_store

    observation = await _execute(_tool(store, "NONEXIST_DB"), keyword="火球术")

    assert "未加载" in observation


@pytest.mark.asyncio
async def test_search_knowledge_empty_keyword(query_store):
    store, make_db = query_store
    db_name = await make_db("SQEMPTY")

    observation = await _execute(_tool(store, db_name), keyword="# &")

    assert "关键词" in observation


@pytest.mark.asyncio
async def test_search_knowledge_rejects_old_filter_syntax(query_store):
    store, make_db = query_store
    db_name = await make_db("SQOLD")

    observation = await _execute(_tool(store, db_name), query="#法师")

    assert observation == "查询格式错误。"


@pytest.mark.asyncio
async def test_search_knowledge_query_unavailable():
    async def resolve_db(user_id, group_id):
        return ""

    tool = build_search_knowledge_tool(None, resolve_db)

    observation = await _execute(tool, keyword="火球术")

    assert "不可用" in observation


@pytest.mark.asyncio
async def test_search_knowledge_detail_index_out_of_range(query_store):
    store, make_db = query_store
    db_name = await make_db("SQINDEX")

    await store.execute(
        db_name,
        "INSERT INTO data VALUES(?,?,?,?,?,?)",
        ("火球术", "Fireball", "PHB", "法术", "塑能", "内容"),
        commit=True,
    )

    observation = await _execute(_tool(store, db_name), keyword="火球术", detail_index=99)

    assert "超出" in observation


@pytest.mark.asyncio
async def test_search_knowledge_build_query_fallback(query_store):
    store, make_db = query_store
    db_name = await make_db("SQFALLBACK")

    await store.execute(
        db_name,
        "INSERT INTO data VALUES(?,?,?,?,?,?)",
        ("火球术", "Fireball", "PHB", "法术", "塑能", "内容"),
        commit=True,
    )

    data = await _execute(
        _tool(store, db_name),
        keyword="不存在的词条",
        query="火球术",
    )

    assert data["total"] == 1


def test_tool_spec_format():
    assert SEARCH_KNOWLEDGE_TOOL.name == "search_knowledge"
    assert SEARCH_KNOWLEDGE_TOOL.description
    properties = SEARCH_KNOWLEDGE_TOOL.args_schema.model_json_schema()["properties"]
    assert {"keyword", "query", "detail_index"}.issubset(properties)
    assert {"tags", "category", "source"}.isdisjoint(properties)
