import pytest
import json

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_list_databases_returns_info(fresh_bot, tmp_path):
    """list_query_databases — 返回数据库列表和默认库"""
    bot, _proxy = fresh_bot

    from module.persona.tools.list_databases import (
        LIST_QUERY_DATABASES_TOOL,
        list_query_databases_executor,
    )
    from module.persona.tools.context import ToolContext

    # 创建测试数据库
    db_name = "LISTDBTEST"
    db_path = str(tmp_path / f"{db_name}.db")
    await bot.db.query.create_empty_database(db_path)
    await bot.db.query.connect_path(db_path)

    try:
        await bot.db.query.execute(
            db_name,
            "INSERT INTO data VALUES(?,?,?,?,?,?)",
            ("测试条目", "TestEntry", "PHB", "法术", "通用", "内容"),
            commit=True,
        )

        async def _resolve_db(user_id, group_id):
            return db_name

        ctx = ToolContext(
            user_id="test_user", group_id="",
            query=bot.db.query, resolve_db=_resolve_db,
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
    finally:
        await bot.db.query.disconnect_database(db_name)


@pytest.mark.asyncio
async def test_list_databases_query_unavailable(fresh_bot):
    """list_query_databases — query 不可用时的降级"""
    bot, _proxy = fresh_bot

    from module.persona.tools.list_databases import list_query_databases_executor
    from module.persona.tools.context import ToolContext

    ctx = ToolContext(user_id="test_user", group_id="")
    result = await list_query_databases_executor({}, ctx)
    assert "不可用" in result
