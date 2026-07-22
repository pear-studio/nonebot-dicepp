"""Shared fixtures for persona integration tests."""

import pytest


@pytest.fixture(autouse=True)
def reset_clock_after_test():
    """Restore the wall clock after tests that install a virtual clock."""
    yield

    from utils.time import WallClock, set_clock

    set_clock(WallClock())


@pytest.fixture
async def temp_db():
    """Provide an initialized in-memory persona store."""
    import aiosqlite

    from module.persona.data.store import PersonaDataStore

    async with (
        aiosqlite.connect(":memory:") as persona_db,
        aiosqlite.connect(":memory:") as core_db,
    ):
        await persona_db.execute("PRAGMA foreign_keys=ON")
        store = PersonaDataStore(":memory:", core_db)
        store._persona_db = persona_db
        await store.ensure_tables()
        yield store
