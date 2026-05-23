import pytest
import json

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_search_knowledge_summary_mode(fresh_bot, tmp_path):
    """search_knowledge — 摘要模式返回 snippet"""
    bot, _proxy = fresh_bot

    from module.persona.tools.search_knowledge import (
        SEARCH_KNOWLEDGE_TOOL,
        search_knowledge_executor,
    )
    from module.persona.tools.context import ToolContext

    db_name = "SQSTEST"
    db_path = str(tmp_path / f"{db_name}.db")
    await bot.db.query.create_empty_database(db_path)
    await bot.db.query.connect_path(db_path)

    try:
        long_content = "这是一个非常长的内容" * 20  # ~200字
        await bot.db.query.execute(
            db_name,
            "INSERT INTO data VALUES(?,?,?,?,?,?)",
            ("火球术", "Fireball", "PHB", "法术", "塑能 3环", long_content),
            commit=True,
        )

        async def _resolve_db(user_id, group_id):
            return db_name

        ctx = ToolContext(
            user_id="test_user", group_id="",
            query=bot.db.query, resolve_db=_resolve_db,
        )
        result = await search_knowledge_executor({"keyword": "火球术"}, ctx)
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
    finally:
        await bot.db.query.disconnect_database(db_name)


@pytest.mark.asyncio
async def test_search_knowledge_detail_mode(fresh_bot, tmp_path):
    """search_knowledge — 详情模式返回完整 content"""
    bot, _proxy = fresh_bot

    from module.persona.tools.search_knowledge import search_knowledge_executor
    from module.persona.tools.context import ToolContext

    db_name = "SQDETAIL"
    db_path = str(tmp_path / f"{db_name}.db")
    await bot.db.query.create_empty_database(db_path)
    await bot.db.query.connect_path(db_path)

    try:
        await bot.db.query.execute(
            db_name,
            "INSERT INTO data VALUES(?,?,?,?,?,?)",
            ("火球术", "Fireball", "PHB", "法术", "塑能 3环", "完整火球术描述内容"),
            commit=True,
        )

        async def _resolve_db(user_id, group_id):
            return db_name

        ctx = ToolContext(
            user_id="test_user", group_id="",
            query=bot.db.query, resolve_db=_resolve_db,
        )
        result = await search_knowledge_executor(
            {"keyword": "火球术", "detail_index": 0}, ctx,
        )
        data = json.loads(result)

        assert data["name"] == "火球术"
        assert data["content"] == "完整火球术描述内容"
        assert "snippet" not in data
    finally:
        await bot.db.query.disconnect_database(db_name)


@pytest.mark.asyncio
async def test_search_knowledge_database_not_found(fresh_bot):
    """search_knowledge — 数据库不存在时的降级"""
    bot, _proxy = fresh_bot

    from module.persona.tools.search_knowledge import search_knowledge_executor
    from module.persona.tools.context import ToolContext

    async def _resolve_db(user_id, group_id):
        return "NONEXIST_DB"

    ctx = ToolContext(
        user_id="test_user", group_id="",
        query=bot.db.query, resolve_db=_resolve_db,
    )
    result = await search_knowledge_executor({"keyword": "火球术"}, ctx)
    assert "未加载" in result


@pytest.mark.asyncio
async def test_search_knowledge_empty_keyword(fresh_bot, tmp_path):
    """search_knowledge — 空关键词降级"""
    bot, _proxy = fresh_bot

    from module.persona.tools.search_knowledge import search_knowledge_executor
    from module.persona.tools.context import ToolContext

    db_name = "SQEMPTY"
    db_path = str(tmp_path / f"{db_name}.db")
    await bot.db.query.create_empty_database(db_path)
    await bot.db.query.connect_path(db_path)

    try:
        async def _resolve_db(user_id, group_id):
            return db_name

        ctx = ToolContext(
            user_id="test_user", group_id="",
            query=bot.db.query, resolve_db=_resolve_db,
        )
        # keyword 只是纯特殊字符
        result = await search_knowledge_executor({"keyword": "# &"}, ctx)
        assert "为空" in result or "关键词" in result
    finally:
        await bot.db.query.disconnect_database(db_name)


@pytest.mark.asyncio
async def test_search_knowledge_query_unavailable(fresh_bot):
    """search_knowledge — query 不可用时的降级"""
    bot, _proxy = fresh_bot

    from module.persona.tools.search_knowledge import search_knowledge_executor
    from module.persona.tools.context import ToolContext

    ctx = ToolContext(user_id="test_user", group_id="")
    result = await search_knowledge_executor({"keyword": "火球术"}, ctx)
    assert "不可用" in result


@pytest.mark.asyncio
async def test_search_knowledge_detail_index_out_of_range(fresh_bot, tmp_path):
    """search_knowledge — detail_index 越界降级"""
    bot, _proxy = fresh_bot

    from module.persona.tools.search_knowledge import search_knowledge_executor
    from module.persona.tools.context import ToolContext

    db_name = "SQINDEX"
    db_path = str(tmp_path / f"{db_name}.db")
    await bot.db.query.create_empty_database(db_path)
    await bot.db.query.connect_path(db_path)

    try:
        await bot.db.query.execute(
            db_name,
            "INSERT INTO data VALUES(?,?,?,?,?,?)",
            ("火球术", "Fireball", "PHB", "法术", "塑能", "内容"),
            commit=True,
        )

        async def _resolve_db(user_id, group_id):
            return db_name

        ctx = ToolContext(
            user_id="test_user", group_id="",
            query=bot.db.query, resolve_db=_resolve_db,
        )
        result = await search_knowledge_executor(
            {"keyword": "火球术", "detail_index": 99}, ctx,
        )
        assert "超出" in result
    finally:
        await bot.db.query.disconnect_database(db_name)


@pytest.mark.asyncio
async def test_search_knowledge_build_query_fallback(fresh_bot, tmp_path):
    """search_knowledge — query fallback 忽略结构化参数"""
    bot, _proxy = fresh_bot

    from module.persona.tools.search_knowledge import search_knowledge_executor
    from module.persona.tools.context import ToolContext

    db_name = "SQFALLBACK"
    db_path = str(tmp_path / f"{db_name}.db")
    await bot.db.query.create_empty_database(db_path)
    await bot.db.query.connect_path(db_path)

    try:
        await bot.db.query.execute(
            db_name,
            "INSERT INTO data VALUES(?,?,?,?,?,?)",
            ("火球术", "Fireball", "PHB", "法术", "塑能", "内容"),
            commit=True,
        )

        async def _resolve_db(user_id, group_id):
            return db_name

        ctx = ToolContext(
            user_id="test_user", group_id="",
            query=bot.db.query, resolve_db=_resolve_db,
        )
        # query 参数存在时应忽略 keyword
        result = await search_knowledge_executor(
            {"keyword": "不存在的词条", "query": "火球术"}, ctx,
        )
        data = json.loads(result)
        assert data["total"] == 1
    finally:
        await bot.db.query.disconnect_database(db_name)
