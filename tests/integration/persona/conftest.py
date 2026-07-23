"""Shared fixtures for persona integration tests."""

import pytest


@pytest.fixture(autouse=True)
def reset_clock_after_test():
    """Restore the wall clock after tests that install a virtual clock."""
    yield

    from plugins.DicePP.utils.time import WallClock, set_clock

    set_clock(WallClock())


@pytest.fixture
async def temp_db():
    """Provide an initialized in-memory persona store."""
    import aiosqlite

    from plugins.DicePP.module.persona.data.store import PersonaDataStore

    async with aiosqlite.connect(":memory:") as core_db:
        async with PersonaDataStore(":memory:", core_db) as store:
            yield store
