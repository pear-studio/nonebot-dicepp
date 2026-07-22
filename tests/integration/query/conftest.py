"""Shared fixtures for query module tests."""

import pytest_asyncio


@pytest_asyncio.fixture
async def query_db(fresh_bot, tmp_path):
    """Factory fixture that creates, connects, and auto-tears-down query databases.

    Usage::

        async def test_something(fresh_bot, query_db):
            bot, _proxy = fresh_bot
            db_name = await query_db("MYDB")
            # use bot.db.query with db_name ...

    On teardown all databases created via this fixture are disconnected.
    """
    bot, _proxy = fresh_bot
    created: list[str] = []

    async def _create(db_name: str) -> str:
        db_path = str(tmp_path / f"{db_name}.db")
        await bot.db.query.create_empty_database(db_path)
        await bot.db.query.connect_path(db_path)
        created.append(db_name)
        return db_name

    yield _create

    for name in created:
        await bot.db.query.disconnect_database(name)
