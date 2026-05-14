import pytest

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_search_basic_keyword(fresh_bot, tmp_path):
    """QueryStore.search() — 基本关键词搜索"""
    bot, _proxy = fresh_bot

    db_name = "SEARCHTEST"
    db_path = str(tmp_path / f"{db_name}.db")

    await bot.db.query.create_empty_database(db_path)
    await bot.db.query.connect_path(db_path)

    try:
        await bot.db.query.execute(
            db_name,
            "INSERT INTO data VALUES(?,?,?,?,?,?)",
            ("火球术", "Fireball", "PHB", "法术", "塑能 3环", "火球术是3环塑能法术..."),
            commit=True,
        )
        await bot.db.query.execute(
            db_name,
            "INSERT INTO data VALUES(?,?,?,?,?,?)",
            ("火箭术", "Fire Arrow", "PHB", "法术", "塑能 2环", "火箭术是2环塑能法术..."),
            commit=True,
        )

        result = await bot.db.query.search(
            databases=[db_name],
            query_tokens=["火球术"],
        )
        assert len(result["results"]) == 1
        assert result["results"][0]["name"] == "火球术"
        assert result["results"][0]["content"] == "火球术是3环塑能法术..."
    finally:
        await bot.db.query.disconnect_database(db_name)


@pytest.mark.asyncio
async def test_search_fulltext(fresh_bot, tmp_path):
    """QueryStore.search() — fulltext 正文搜索"""
    bot, _proxy = fresh_bot

    db_name = "SEARCHFULL"
    db_path = str(tmp_path / f"{db_name}.db")

    await bot.db.query.create_empty_database(db_path)
    await bot.db.query.connect_path(db_path)

    try:
        await bot.db.query.execute(
            db_name,
            "INSERT INTO data VALUES(?,?,?,?,?,?)",
            ("火球术", "Fireball", "PHB", "法术", "塑能", "3环塑能法术，造成8d6火焰伤害"),
            commit=True,
        )
        await bot.db.query.execute(
            db_name,
            "INSERT INTO data VALUES(?,?,?,?,?,?)",
            ("寒冰锥", "Cone of Cold", "PHB", "法术", "塑能", "5环塑能法术，造成8d8寒冷伤害"),
            commit=True,
        )

        # fulltext=False 搜 "火焰" 不命中内容（只搜名称/英文）
        result_name = await bot.db.query.search(
            databases=[db_name],
            query_tokens=["火焰"],
            fulltext=False,
        )
        assert len(result_name["results"]) == 0

        # fulltext=True 搜 "火焰" 命中内容
        result_full = await bot.db.query.search(
            databases=[db_name],
            query_tokens=["火焰"],
            fulltext=True,
        )
        assert len(result_full["results"]) == 1
        assert result_full["results"][0]["name"] == "火球术"
    finally:
        await bot.db.query.disconnect_database(db_name)


@pytest.mark.asyncio
async def test_search_redirect_resolution(fresh_bot, tmp_path):
    """QueryStore.search() — redirect 解析（搜索词命中 redirect 表而非 data 表）"""
    bot, _proxy = fresh_bot

    db_name = "SEARCHREDIR"
    db_path = str(tmp_path / f"{db_name}.db")

    await bot.db.query.create_empty_database(db_path)
    await bot.db.query.connect_path(db_path)

    try:
        await bot.db.query.execute(
            db_name,
            "INSERT INTO data VALUES(?,?,?,?,?,?)",
            ("火球术", "Fireball", "PHB", "法术", "塑能", "完整内容"),
            commit=True,
        )
        # 别名与真实名称不重叠，确保只有 redirect 能命中
        await bot.db.query.execute(
            db_name,
            "INSERT INTO redirect VALUES(?,?)",
            ("大火球", "火球术"),
            commit=True,
        )

        result = await bot.db.query.search(
            databases=[db_name],
            query_tokens=["大火球"],
        )
        assert len(result["results"]) == 1
        assert result["results"][0]["name"] == "火球术"
        assert result["results"][0]["redirect_by"] == "大火球"
    finally:
        await bot.db.query.disconnect_database(db_name)


@pytest.mark.asyncio
async def test_search_dedup(fresh_bot, tmp_path):
    """QueryStore.search() — 去重（hash_word 相同只保留一条）"""
    bot, _proxy = fresh_bot

    db_name = "SEARCHDEDUP"
    db_path = str(tmp_path / f"{db_name}.db")

    await bot.db.query.create_empty_database(db_path)
    await bot.db.query.connect_path(db_path)

    try:
        await bot.db.query.execute(
            db_name,
            "INSERT INTO data VALUES(?,?,?,?,?,?)",
            ("火球术", "Fireball", "PHB", "法术", "塑能", "内容A"),
            commit=True,
        )
        # 同名+同来源+同分类 = 同 hash_word
        await bot.db.query.execute(
            db_name,
            "INSERT INTO data VALUES(?,?,?,?,?,?)",
            ("火球术", "Fireball", "PHB", "法术", "塑能 3环", "内容B"),
            commit=True,
        )

        result = await bot.db.query.search(
            databases=[db_name],
            query_tokens=["火球术"],
        )
        assert len(result["results"]) == 1
    finally:
        await bot.db.query.disconnect_database(db_name)


@pytest.mark.asyncio
async def test_search_max_total_exceeded(fresh_bot, tmp_path):
    """QueryStore.search() — 超限抛 QueryStoreError"""
    bot, _proxy = fresh_bot

    from core.data.query_store import QueryStoreError

    db_name = "SEARCHMAX"
    db_path = str(tmp_path / f"{db_name}.db")

    await bot.db.query.create_empty_database(db_path)
    await bot.db.query.connect_path(db_path)

    try:
        # 插入 5 条，设 max_total=3
        for i in range(5):
            await bot.db.query.execute(
                db_name,
                "INSERT INTO data VALUES(?,?,?,?,?,?)",
                (f"条目{i}", f"Entry{i}", "PHB", "法术", "通用", f"内容{i}"),
                commit=True,
            )
        with pytest.raises(QueryStoreError):
            await bot.db.query.search(
                databases=[db_name],
                query_tokens=["条目"],
                max_total=3,
            )
    finally:
        await bot.db.query.disconnect_database(db_name)


@pytest.mark.asyncio
async def test_get_database_info(fresh_bot, tmp_path):
    """QueryStore.get_database_info() — 返回库元数据"""
    bot, _proxy = fresh_bot

    db_name = "INFOTEST"
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
        await bot.db.query.execute(
            db_name,
            "INSERT INTO data VALUES(?,?,?,?,?,?)",
            ("长剑", "Longsword", "PHB", "武器", "军用", "内容"),
            commit=True,
        )

        info = await bot.db.query.get_database_info(db_name)
        assert info["name"] == db_name
        assert info["rows"] == 2
        assert "法术" in info["categories"]
        assert "武器" in info["categories"]

        # 未加载库返回 None
        assert await bot.db.query.get_database_info("NONEXIST") is None
    finally:
        await bot.db.query.disconnect_database(db_name)


@pytest.mark.asyncio
async def test_search_empty_tokens(fresh_bot):
    """QueryStore.search() — 空 token 列表直接返回空"""
    bot, _proxy = fresh_bot

    result = await bot.db.query.search(
        databases=["DND5E混合"],
        query_tokens=[],
    )
    assert result == {"results": [], "total": 0}


@pytest.mark.asyncio
async def test_search_tag_prefix(fresh_bot, tmp_path):
    """QueryStore.search() — #标签前缀过滤"""
    bot, _proxy = fresh_bot

    db_name = "TAGTEST"
    db_path = str(tmp_path / f"{db_name}.db")

    await bot.db.query.create_empty_database(db_path)
    await bot.db.query.connect_path(db_path)

    try:
        await bot.db.query.execute(
            db_name,
            "INSERT INTO data VALUES(?,?,?,?,?,?)",
            ("火球术", "Fireball", "PHB", "法术", "塑能 3环", "内容"),
            commit=True,
        )
        await bot.db.query.execute(
            db_name,
            "INSERT INTO data VALUES(?,?,?,?,?,?)",
            ("治疗伤口", "Cure Wounds", "PHB", "法术", "治愈 1环", "内容"),
            commit=True,
        )

        # #塑能 应只命中火球术
        result = await bot.db.query.search(
            databases=[db_name],
            query_tokens=["#塑能"],
        )
        assert len(result["results"]) == 1
        assert result["results"][0]["name"] == "火球术"
    finally:
        await bot.db.query.disconnect_database(db_name)


@pytest.mark.asyncio
async def test_search_category_prefix(fresh_bot, tmp_path):
    """QueryStore.search() — &分类前缀过滤"""
    bot, _proxy = fresh_bot

    db_name = "CATTEST"
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
        await bot.db.query.execute(
            db_name,
            "INSERT INTO data VALUES(?,?,?,?,?,?)",
            ("长剑", "Longsword", "PHB", "武器", "军用", "内容"),
            commit=True,
        )

        # &法术 应只命中火球术
        result = await bot.db.query.search(
            databases=[db_name],
            query_tokens=["&法术"],
        )
        assert len(result["results"]) == 1
        assert result["results"][0]["name"] == "火球术"
    finally:
        await bot.db.query.disconnect_database(db_name)


@pytest.mark.asyncio
async def test_search_homebrew_merge(fresh_bot, tmp_path):
    """QueryStore.search() — HB 私设库合并（同名覆盖 + 空内容丢弃）"""
    bot, _proxy = fresh_bot

    main_db = "HBMAIN"
    hb_db = "HBTEST"

    main_path = str(tmp_path / f"{main_db}.db")
    hb_path = str(tmp_path / f"{hb_db}.db")

    await bot.db.query.create_empty_database(main_path)
    await bot.db.query.connect_path(main_path)
    await bot.db.query.create_empty_database(hb_path)
    await bot.db.query.connect_path(hb_path)

    try:
        # 主库条目
        await bot.db.query.execute(
            main_db,
            "INSERT INTO data VALUES(?,?,?,?,?,?)",
            ("火球术", "Fireball", "PHB", "法术", "塑能 3环", "主库火球术描述"),
            commit=True,
        )
        await bot.db.query.execute(
            main_db,
            "INSERT INTO data VALUES(?,?,?,?,?,?)",
            ("寒冰锥", "Cone of Cold", "PHB", "法术", "塑能 5环", "主库寒冰锥描述"),
            commit=True,
        )

        # HB 库：同名覆盖火球术 + 空内容条目
        await bot.db.query.execute(
            hb_db,
            "INSERT INTO data VALUES(?,?,?,?,?,?)",
            ("火球术", "Fireball", "PHB", "法术", "私设 3环", "私设火球术描述"),
            commit=True,
        )
        # 空内容条目 — 应被丢弃
        await bot.db.query.execute(
            hb_db,
            "INSERT INTO data VALUES(?,?,?,?,?,?)",
            ("空条目", "Empty", "PHB", "法术", "私设", ""),
            commit=True,
        )

        result = await bot.db.query.search(
            databases=[main_db, hb_db],
            query_tokens=["火球术"],
        )
        assert len(result["results"]) == 1
        assert result["results"][0]["content"] == "私设火球术描述"

        # 空内容 HB 条目不应出现
        result_all = await bot.db.query.search(
            databases=[main_db, hb_db],
            query_tokens=["空条目"],
        )
        assert len(result_all["results"]) == 0
    finally:
        await bot.db.query.disconnect_database(main_db)
        await bot.db.query.disconnect_database(hb_db)


@pytest.mark.asyncio
async def test_search_empty_databases(fresh_bot):
    """QueryStore.search() — 空 databases 列表返回空"""
    bot, _proxy = fresh_bot

    result = await bot.db.query.search(
        databases=[],
        query_tokens=["火球术"],
    )
    assert result == {"results": [], "total": 0}
