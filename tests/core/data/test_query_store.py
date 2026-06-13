import pytest

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_botdatabase_exposes_query_store_and_can_connect(tmp_path):
    """Pure unit test: instantiate QueryStore directly with a temp base_dir."""
    import os
    from core.data.query_store import QueryStore

    store = QueryStore()

    assert hasattr(store, "connect_path")
    assert hasattr(store, "create_empty_database")

    db_name = "DNDSTORE_TEST"
    db_path = str(tmp_path / f"{db_name}.db")

    ok = await store.create_empty_database(db_path)
    assert ok is True

    await store.connect_path(db_path)
    assert store.has_database(db_name) is True

    await store.disconnect_database(db_name)
    assert store.has_database(db_name) is False

