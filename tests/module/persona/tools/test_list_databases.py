"""list_query_databases 工具 — 单元测试

直接使用独立的 QueryStore + ToolContext，不依赖 fresh_bot。
"""

import pytest

pytestmark = pytest.mark.unit  # 原 integration 测试已降级为 QueryStore 单元测试，不再依赖 fresh_bot
import json

from module.persona.tools.list_databases import (
    LIST_QUERY_DATABASES_TOOL,
    list_query_databases_executor,
)
from module.persona.tools.context import ToolContext


@pytest.mark.asyncio
async def test_list_databases_returns_info(query_store):
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
    # 找到我们创建的库
    found = [d for d in data["databases"] if d["name"] == db_name]
    assert len(found) == 1
    assert found[0]["rows"] == 1
    assert "法术" in found[0]["categories"]


@pytest.mark.asyncio
async def test_list_databases_query_unavailable():
    """list_query_databases — query 不可用时的降级"""
    ctx = ToolContext(user_id="test_user", group_id="")
    result = await list_query_databases_executor({}, ctx)
    assert "不可用" in result
