import pytest

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_botdatabase_exposes_query_store_and_can_connect(fresh_bot, tmp_path):
    bot, _proxy = fresh_bot

    assert hasattr(bot.db, "query")
    assert hasattr(bot.db.query, "connect_path")
    assert hasattr(bot.db.query, "create_empty_database")

    db_name = "DNDSTORE_TEST"
    db_path = str(tmp_path / f"{db_name}.db")

    ok = await bot.db.query.create_empty_database(db_path)
    assert ok is True

    await bot.db.query.connect_path(db_path)
    assert bot.db.query.has_database(db_name) is True

    await bot.db.query.disconnect_database(db_name)
    assert bot.db.query.has_database(db_name) is False

