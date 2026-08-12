import pytest

from plugins.DicePP.core.data.query_store import QueryStoreError

@pytest.mark.asyncio
async def test_search_basic_keyword(fresh_bot, query_db):
    """QueryStore.search() — 基本关键词搜索"""
    bot, _proxy = fresh_bot
    db_name = await query_db("SEARCHTEST")

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
        database=db_name,
        query_tokens=["火球术"],
    )
    assert len(result["results"]) == 1
    assert result["results"][0]["name"] == "火球术"
    assert result["results"][0]["content"] == "火球术是3环塑能法术..."


@pytest.mark.asyncio
async def test_search_fulltext(fresh_bot, query_db):
    """QueryStore.search() — fulltext 正文搜索"""
    bot, _proxy = fresh_bot
    db_name = await query_db("SEARCHFULL")

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
        database=db_name,
        query_tokens=["火焰"],
        fulltext=False,
    )
    assert len(result_name["results"]) == 0

    # fulltext=True 搜 "火焰" 命中内容
    result_full = await bot.db.query.search(
        database=db_name,
        query_tokens=["火焰"],
        fulltext=True,
    )
    assert len(result_full["results"]) == 1
    assert result_full["results"][0]["name"] == "火球术"


@pytest.mark.asyncio
async def test_search_redirect_resolution(fresh_bot, query_db):
    """QueryStore.search() — redirect 解析（搜索词命中 redirect 表而非 data 表）"""
    bot, _proxy = fresh_bot
    db_name = await query_db("SEARCHREDIR")

    await bot.db.query.execute(
        db_name,
        "INSERT INTO data VALUES(?,?,?,?,?,?)",
        ("火球术", "Fireball", "PHB", "法术", "塑能", "完整内容"),
        commit=True,
    )
    await bot.db.query.execute(
        db_name,
        "INSERT INTO data VALUES(?,?,?,?,?,?)",
        ("强效火球术", "Greater Fireball", "TEST", "法术", "塑能", "不应被别名命中"),
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
        database=db_name,
        query_tokens=["大火球"],
    )
    assert len(result["results"]) == 1
    assert result["results"][0]["name"] == "火球术"
    assert result["results"][0]["redirect_by"] == "大火球"


@pytest.mark.asyncio
async def test_search_dedup(fresh_bot, query_db):
    """QueryStore.search() — 同名同来源只保留第一条。"""
    bot, _proxy = fresh_bot
    db_name = await query_db("SEARCHDEDUP")

    await bot.db.query.execute(
        db_name,
        "INSERT INTO data VALUES(?,?,?,?,?,?)",
        ("火球术", "Fireball", "PHB", "法术", "塑能", "内容A"),
        commit=True,
    )
    # 分类和标签不参与身份判断，后续同名同来源内容会被隐藏。
    await bot.db.query.execute(
        db_name,
        "INSERT INTO data VALUES(?,?,?,?,?,?)",
        ("火球术", "Fireball", "PHB", "法术", "塑能 3环", "内容B"),
        commit=True,
    )

    result = await bot.db.query.search(
        database=db_name,
        query_tokens=["火球术"],
    )
    assert len(result["results"]) == 1


@pytest.mark.asyncio
async def test_dedup_identity_is_a_tuple_not_a_joined_string(fresh_bot, query_db):
    bot, _proxy = fresh_bot
    db_name = await query_db("SEARCHIDENTITY")
    await bot.db.query.executemany(
        db_name,
        "INSERT INTO data VALUES(?,?,?,?,?,?)",
        [
            ("a#b", "", "c", "", "", "first"),
            ("a", "", "b#c", "", "", "second"),
        ],
        commit=True,
    )

    result = await bot.db.query.search(
        database=db_name,
        query_tokens=["a"],
        fulltext=True,
    )

    assert [(row["name"], row["source"]) for row in result["results"]] == [
        ("a#b", "c"),
        ("a", "b#c"),
    ]


@pytest.mark.asyncio
async def test_exact_name_wins_beyond_fuzzy_result_limit(fresh_bot, query_db):
    bot, _proxy = fresh_bot
    db_name = await query_db("SEARCHEXACTLATE")
    await bot.db.query.executemany(
        db_name,
        "INSERT INTO data VALUES(?,?,?,?,?,?)",
        [
            (f"目标词条变体{i}", "", f"SRC{i}", "", "", f"content {i}")
            for i in range(2002)
        ]
        + [("目标词条", "", "EXACT", "", "", "exact content")],
        commit=True,
    )

    result = await bot.db.query.search(
        database=db_name,
        query_tokens=["目标词条"],
        max_total=1000,
    )

    assert result["total"] == 1
    assert result["results"][0]["source"] == "EXACT"


@pytest.mark.asyncio
async def test_search_max_total_exceeded(fresh_bot, query_db):
    """QueryStore.search() — 超限抛 QueryStoreError"""
    bot, _proxy = fresh_bot
    from plugins.DicePP.core.data.query_store import QueryStoreError

    db_name = await query_db("SEARCHMAX")

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
            database=db_name,
            query_tokens=["条目"],
            max_total=3,
        )


@pytest.mark.asyncio
async def test_get_database_info(fresh_bot, query_db):
    """QueryStore.get_database_info() — 返回库元数据"""
    bot, _proxy = fresh_bot
    db_name = await query_db("INFOTEST")

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
    assert info == {"name": db_name, "rows": 2}

    # 未加载库返回 None
    assert await bot.db.query.get_database_info("NONEXIST") is None


@pytest.mark.asyncio
async def test_search_empty_tokens(fresh_bot):
    """QueryStore.search() — 空 token 列表直接返回空"""
    bot, _proxy = fresh_bot

    result = await bot.db.query.search(
        database="DND5E混合",
        query_tokens=[],
    )
    assert result == {"results": [], "total": 0}


@pytest.mark.asyncio
async def test_search_tag_prefix_is_rejected(fresh_bot, query_db):
    """QueryStore.search() — # 不再作为标签/来源筛选语法。"""
    bot, _proxy = fresh_bot
    db_name = await query_db("TAGTEST")

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

    with pytest.raises(QueryStoreError, match="查询格式错误"):
        await bot.db.query.search(database=db_name, query_tokens=["#塑能"])


@pytest.mark.asyncio
async def test_search_category_prefix_is_rejected(fresh_bot, query_db):
    """QueryStore.search() — & 不再作为分类筛选语法。"""
    bot, _proxy = fresh_bot
    db_name = await query_db("CATTEST")

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

    with pytest.raises(QueryStoreError, match="查询格式错误"):
        await bot.db.query.search(database=db_name, query_tokens=["&法术"])


# ── Q56: / OR 分隔符 ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_search_or_separator_returns_both(fresh_bot, query_db):
    """/ 分隔的两个关键词应命中各自匹配的条目"""
    bot, _proxy = fresh_bot
    db_name = await query_db("ORTEST")

    await bot.db.query.execute(
        db_name,
        "INSERT INTO data VALUES(?,?,?,?,?,?)",
        ("火球术", "Fireball", "PHB", "法术", "塑能", "火球术内容"),
        commit=True,
    )
    await bot.db.query.execute(
        db_name,
        "INSERT INTO data VALUES(?,?,?,?,?,?)",
        ("寒冰锥", "Ice", "PHB", "法术", "塑能", "寒冰锥内容"),
        commit=True,
    )

    result = await bot.db.query.search(
        database=db_name,
        query_tokens=["火球术/寒冰锥"],
    )
    assert len(result["results"]) == 2
    names = {r["name"] for r in result["results"]}
    assert "火球术" in names
    assert "寒冰锥" in names


@pytest.mark.asyncio
async def test_search_or_separator_single_matches_one(fresh_bot, query_db):
    """/ 分隔后只命中一个关键词时仍返回正确"""
    bot, _proxy = fresh_bot
    db_name = await query_db("ORSINGLE")

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

    # 火球术/不存在 → 应只命中火球术
    result = await bot.db.query.search(
        database=db_name,
        query_tokens=["火球术/不存在"],
    )
    assert len(result["results"]) == 1
    assert result["results"][0]["name"] == "火球术"


# ── Q57: limit/offset 分页 ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_search_limit_offset_first_page(fresh_bot, query_db):
    """limit=2, offset=0 应返回前 2 条"""
    bot, _proxy = fresh_bot
    db_name = await query_db("PAGE1")

    for i in range(5):
        await bot.db.query.execute(
            db_name,
            "INSERT INTO data VALUES(?,?,?,?,?,?)",
            (f"条目{i}", f"Entry{i}", "PHB", "法术", "通用", f"内容{i}"),
            commit=True,
        )

    result = await bot.db.query.search(
        database=db_name,
        query_tokens=["条目"],
        limit=2,
        offset=0,
    )
    assert len(result["results"]) == 2
    assert result["total"] == 5


@pytest.mark.asyncio
async def test_search_limit_offset_second_page(fresh_bot, query_db):
    """limit=2, offset=2 应返回第 3-4 条"""
    bot, _proxy = fresh_bot
    db_name = await query_db("PAGE2")

    for i in range(5):
        await bot.db.query.execute(
            db_name,
            "INSERT INTO data VALUES(?,?,?,?,?,?)",
            (f"条目{i}", f"Entry{i}", "PHB", "法术", "通用", f"内容{i}"),
            commit=True,
        )

    result = await bot.db.query.search(
        database=db_name,
        query_tokens=["条目"],
        limit=2,
        offset=2,
    )
    assert len(result["results"]) == 2


@pytest.mark.asyncio
async def test_search_limit_offset_beyond_total(fresh_bot, query_db):
    """offset 超出总数应返回空列表"""
    bot, _proxy = fresh_bot
    db_name = await query_db("PAGE3")

    for i in range(3):
        await bot.db.query.execute(
            db_name,
            "INSERT INTO data VALUES(?,?,?,?,?,?)",
            (f"条目{i}", f"Entry{i}", "PHB", "法术", "通用", f"内容{i}"),
            commit=True,
        )

    result = await bot.db.query.search(
        database=db_name,
        query_tokens=["条目"],
        limit=5,
        offset=10,
    )
    assert len(result["results"]) == 0
    assert result["total"] == 3
