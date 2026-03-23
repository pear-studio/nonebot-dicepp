import pytest

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_query_search_exact_name_regexp_params(fresh_bot, tmp_path):
    bot, _proxy = fresh_bot

    from module.query.query_command import QueryCommand

    db_name = "DNDTEST"
    db_path = str(tmp_path / f"{db_name}.db")

    await bot.db.query.create_empty_database(db_path)
    await bot.db.query.connect_path(db_path)

    try:
        await bot.db.query.execute(
            db_name,
            "INSERT INTO data VALUES(?,?,?,?,?,?)",
            ("Fireball", "FireballEN", "PHB", "法术", "火焰", "Boom!"),
            commit=True,
        )

        query_cmd = QueryCommand(bot)
        results = await query_cmd.search_item(db_name, ["Fireball"], search_mode=0)

        assert len(results) == 1
        assert results[0].data_name == "Fireball"
    finally:
        await bot.db.query.disconnect_database(db_name)


@pytest.mark.asyncio
async def test_query_search_negative_excludes(fresh_bot, tmp_path):
    bot, _proxy = fresh_bot

    from module.query.query_command import QueryCommand

    db_name = "DNDTEST_NEG"
    db_path = str(tmp_path / f"{db_name}.db")

    await bot.db.query.create_empty_database(db_path)
    await bot.db.query.connect_path(db_path)

    try:
        await bot.db.query.execute(
            db_name,
            "INSERT INTO data VALUES(?,?,?,?,?,?)",
            ("Fireball", "FireballEN", "PHB", "法术", "火焰", "Boom!"),
            commit=True,
        )

        query_cmd = QueryCommand(bot)
        results = await query_cmd.search_item(db_name, ["-Fireball"], search_mode=0)
        assert results == []
    finally:
        await bot.db.query.disconnect_database(db_name)


@pytest.mark.asyncio
async def test_query_search_redirect_by_alias(fresh_bot, tmp_path):
    bot, _proxy = fresh_bot

    from module.query.query_command import QueryCommand

    db_name = "DNDTEST_REDIRECT"
    db_path = str(tmp_path / f"{db_name}.db")

    await bot.db.query.create_empty_database(db_path)
    await bot.db.query.connect_path(db_path)

    try:
        # 真实条目
        await bot.db.query.execute(
            db_name,
            "INSERT INTO data VALUES(?,?,?,?,?,?)",
            ("Canon", "CanonEN", "PHB", "法术", "通用", "Real content"),
            commit=True,
        )
        # 别名 -> 真实条目名称
        await bot.db.query.execute(
            db_name,
            "INSERT INTO redirect VALUES(?,?)",
            ("Alias", "Canon"),
            commit=True,
        )

        query_cmd = QueryCommand(bot)
        results = await query_cmd.search_item(db_name, ["Alias"], search_mode=0)

        assert len(results) == 1
        assert results[0].data_name == "Canon"
        assert results[0].redirect_by == "Alias"
    finally:
        await bot.db.query.disconnect_database(db_name)


@pytest.mark.asyncio
async def test_query_search_apostrophe_is_safe_with_params(fresh_bot, tmp_path):
    bot, _proxy = fresh_bot

    from module.query.query_command import QueryCommand

    db_name = "DNDTEST_QUOTE"
    db_path = str(tmp_path / f"{db_name}.db")

    await bot.db.query.create_empty_database(db_path)
    await bot.db.query.connect_path(db_path)

    try:
        await bot.db.query.execute(
            db_name,
            "INSERT INTO data VALUES(?,?,?,?,?,?)",
            ("O'Reilly", "OReillyEN", "PHB", "法术", "通用", "Quoted content"),
            commit=True,
        )

        query_cmd = QueryCommand(bot)
        results = await query_cmd.search_item(db_name, ["O'Reilly"], search_mode=0)

        assert len(results) == 1
        assert results[0].data_name == "O'Reilly"
    finally:
        await bot.db.query.disconnect_database(db_name)

