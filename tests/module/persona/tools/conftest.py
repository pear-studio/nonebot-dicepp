"""Shared fixtures for persona tool unit tests.

这些工具的 executor 只依赖一个 QueryStore 实例 + ToolContext，
不需要启动完整的 Bot，因此提供独立的 query_store fixture，避免
拉起 fresh_bot 的额外开销与耦合。
"""

import pytest_asyncio

from core.data.query_store import QueryStore


@pytest_asyncio.fixture
async def query_store(tmp_path):
    """提供独立的 QueryStore 实例 + 数据库工厂。

    Returns:
        (store, make_db)，其中 make_db(name) -> str 创建并连接空库，
        返回 db_name。fixture 退出时自动关闭所有连接。
    """
    store = QueryStore(base_dir=str(tmp_path))
    created: list[str] = []

    async def _create(db_name: str) -> str:
        db_path = str(tmp_path / f"{db_name}.db")
        await store.create_empty_database(db_path)
        await store.connect_path(db_path)
        created.append(db_name)
        return db_name

    try:
        yield store, _create
    finally:
        await store.close_all()
