import pytest

pytestmark = pytest.mark.integration


# ── Q55: = 精确匹配前缀 ─────────────────────────────────────────────────────

@pytest.mark.unit
class TestGenerateSearchSqlRegexp:
    """_generate_search_sql_regexp 精确匹配 = 前缀测试"""

    def test_equal_prefix_generates_exact_match(self):
        """=前缀应生成 ^xxx$ 精确匹配模式"""
        from core.data.query_store import QueryStore
        sql, params = QueryStore._generate_search_sql_regexp(["=火球术"])
        assert sql == "名称 regexp ?"
        assert params == ["^火球术$"]

    def test_equal_prefix_with_regex_chars(self):
        """=前缀应转义正则特殊字符"""
        from core.data.query_store import QueryStore
        sql, params = QueryStore._generate_search_sql_regexp(["=(力量)"])
        assert params == ["^\\(力量\\)$"]

    def test_equal_prefix_ignores_prefix_only(self):
        """单独的 = 不应触发 exact match 逻辑"""
        from core.data.query_store import QueryStore
        sql, params = QueryStore._generate_search_sql_regexp(["="])
        # len("=") == 1, 所以走普通文本路径，不生成 ^$ 包裹
        assert params == ["="]

    # ── Q56 辅助：OR 分隔在 regex 层面 ───────────────────────────────────

    def test_multiple_commands_generate_or_pattern(self):
        """多个关键词应生成 | 连接的 OR 正则"""
        from core.data.query_store import QueryStore
        sql, params = QueryStore._generate_search_sql_regexp(["火球术", "寒冰锥"])
        assert params == ["火球术|寒冰锥"]

    def test_combined_equal_and_normal_pattern(self):
        """混合 =精确匹配与普通关键词"""
        from core.data.query_store import QueryStore
        sql, params = QueryStore._generate_search_sql_regexp(["=火球术", "寒冰锥"])
        assert params == ["^火球术$|寒冰锥"]


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
        databases=[db_name],
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


@pytest.mark.asyncio
async def test_search_dedup(fresh_bot, query_db):
    """QueryStore.search() — 去重（hash_word 相同只保留一条）"""
    bot, _proxy = fresh_bot
    db_name = await query_db("SEARCHDEDUP")

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


@pytest.mark.asyncio
async def test_search_max_total_exceeded(fresh_bot, query_db):
    """QueryStore.search() — 超限抛 QueryStoreError"""
    bot, _proxy = fresh_bot
    from core.data.query_store import QueryStoreError

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
            databases=[db_name],
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
    assert info["name"] == db_name
    assert info["rows"] == 2
    assert "法术" in info["categories"]
    assert "武器" in info["categories"]

    # 未加载库返回 None
    assert await bot.db.query.get_database_info("NONEXIST") is None


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
async def test_search_tag_prefix(fresh_bot, query_db):
    """QueryStore.search() — #标签前缀过滤"""
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

    # #塑能 应只命中火球术
    result = await bot.db.query.search(
        databases=[db_name],
        query_tokens=["#塑能"],
    )
    assert len(result["results"]) == 1
    assert result["results"][0]["name"] == "火球术"


@pytest.mark.asyncio
async def test_search_category_prefix(fresh_bot, query_db):
    """QueryStore.search() — &分类前缀过滤"""
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

    # &法术 应只命中火球术
    result = await bot.db.query.search(
        databases=[db_name],
        query_tokens=["&法术"],
    )
    assert len(result["results"]) == 1
    assert result["results"][0]["name"] == "火球术"


@pytest.mark.asyncio
async def test_search_homebrew_merge(fresh_bot, query_db):
    """QueryStore.search() — HB 私设库合并（同名覆盖 + 空内容丢弃）"""
    bot, _proxy = fresh_bot

    main_db = await query_db("HBMAIN")
    hb_db = await query_db("HBTEST")

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


@pytest.mark.asyncio
async def test_search_empty_databases(fresh_bot):
    """QueryStore.search() — 空 databases 列表返回空"""
    bot, _proxy = fresh_bot

    result = await bot.db.query.search(
        databases=[],
        query_tokens=["火球术"],
    )
    assert result == {"results": [], "total": 0}


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
        databases=[db_name],
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
        databases=[db_name],
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
        databases=[db_name],
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
        databases=[db_name],
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
        databases=[db_name],
        query_tokens=["条目"],
        limit=5,
        offset=10,
    )
    assert len(result["results"]) == 0
    assert result["total"] == 3
