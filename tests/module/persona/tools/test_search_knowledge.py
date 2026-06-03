"""search_knowledge 工具 — 单元测试

直接使用独立的 QueryStore + ToolContext，不依赖 fresh_bot。
被测的 search_knowledge_executor 只依赖 ctx.query / ctx.resolve_db，
完整 bot 启停在这里是不必要的负担。
"""

import pytest

pytestmark = pytest.mark.unit  # 原 integration 测试已降级为 QueryStore 单元测试，不再依赖 fresh_bot
import json

from module.persona.tools.search_knowledge import (
    SEARCH_KNOWLEDGE_TOOL,
    search_knowledge_executor,
)
from module.persona.tools.context import ToolContext


def _ctx_with(store, db_name: str = "") -> ToolContext:
    async def _resolve_db(user_id, group_id):
        return db_name

    return ToolContext(
        user_id="test_user", group_id="",
        query=store, resolve_db=_resolve_db,
    )


@pytest.mark.asyncio
async def test_search_knowledge_summary_mode(query_store):
    """search_knowledge — 摘要模式返回 snippet"""
    store, make_db = query_store
    db_name = await make_db("SQSTEST")

    long_content = "这是一个非常长的内容" * 20  # ~200字
    await store.execute(
        db_name,
        "INSERT INTO data VALUES(?,?,?,?,?,?)",
        ("火球术", "Fireball", "PHB", "法术", "塑能 3环", long_content),
        commit=True,
    )

    result = await search_knowledge_executor({"keyword": "火球术"}, _ctx_with(store, db_name))
    data = json.loads(result)

    assert "results" in data
    assert data["total"] == 1
    item = data["results"][0]
    assert item["name"] == "火球术"
    assert item["source"] == "PHB"
    assert item["catalogue"] == "法术"
    assert "snippet" in item
    # snippet 应被截断到 150 字以内
    assert len(item["snippet"]) <= 153  # 150 + "..."
    assert "content" not in item  # 摘要模式不含完整内容


@pytest.mark.asyncio
async def test_search_knowledge_detail_mode(query_store):
    """search_knowledge — 详情模式返回完整 content"""
    store, make_db = query_store
    db_name = await make_db("SQDETAIL")

    await store.execute(
        db_name,
        "INSERT INTO data VALUES(?,?,?,?,?,?)",
        ("火球术", "Fireball", "PHB", "法术", "塑能 3环", "完整火球术描述内容"),
        commit=True,
    )

    result = await search_knowledge_executor(
        {"keyword": "火球术", "detail_index": 0}, _ctx_with(store, db_name),
    )
    data = json.loads(result)

    assert data["name"] == "火球术"
    assert data["content"] == "完整火球术描述内容"
    assert "snippet" not in data


@pytest.mark.asyncio
async def test_search_knowledge_database_not_found(query_store):
    """search_knowledge — 数据库不存在时的降级"""
    store, _ = query_store

    result = await search_knowledge_executor(
        {"keyword": "火球术"}, _ctx_with(store, "NONEXIST_DB"),
    )
    assert "未加载" in result


@pytest.mark.asyncio
async def test_search_knowledge_empty_keyword(query_store):
    """search_knowledge — 空关键词降级"""
    store, make_db = query_store
    db_name = await make_db("SQEMPTY")

    # keyword 只是纯特殊字符
    result = await search_knowledge_executor({"keyword": "# &"}, _ctx_with(store, db_name))
    assert "为空" in result or "关键词" in result


@pytest.mark.asyncio
async def test_search_knowledge_query_unavailable():
    """search_knowledge — query 不可用时的降级"""
    ctx = ToolContext(user_id="test_user", group_id="")
    result = await search_knowledge_executor({"keyword": "火球术"}, ctx)
    assert "不可用" in result


@pytest.mark.asyncio
async def test_search_knowledge_detail_index_out_of_range(query_store):
    """search_knowledge — detail_index 越界降级"""
    store, make_db = query_store
    db_name = await make_db("SQINDEX")

    await store.execute(
        db_name,
        "INSERT INTO data VALUES(?,?,?,?,?,?)",
        ("火球术", "Fireball", "PHB", "法术", "塑能", "内容"),
        commit=True,
    )

    result = await search_knowledge_executor(
        {"keyword": "火球术", "detail_index": 99}, _ctx_with(store, db_name),
    )
    assert "超出" in result


@pytest.mark.asyncio
async def test_search_knowledge_build_query_fallback(query_store):
    """search_knowledge — query fallback 忽略结构化参数"""
    store, make_db = query_store
    db_name = await make_db("SQFALLBACK")

    await store.execute(
        db_name,
        "INSERT INTO data VALUES(?,?,?,?,?,?)",
        ("火球术", "Fireball", "PHB", "法术", "塑能", "内容"),
        commit=True,
    )

    # query 参数存在时应忽略 keyword
    result = await search_knowledge_executor(
        {"keyword": "不存在的词条", "query": "火球术"}, _ctx_with(store, db_name),
    )
    data = json.loads(result)
    assert data["total"] == 1
