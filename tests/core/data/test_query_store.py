import os
import pytest
import aiosqlite

pytestmark = pytest.mark.integration

QUERY_DATA_FIELD_LIST = ["名称", "英文", "来源", "分类", "标签", "内容"]


async def _create_test_db(path: str, rows: list[tuple]) -> None:
    """在 path 处创建 query 数据库并插入数据行。"""
    conn = await aiosqlite.connect(path)
    await conn.execute("PRAGMA journal_mode=WAL")
    await conn.execute(
        "CREATE TABLE data ("
        + ",".join(f"{f} TEXT DEFAULT ('')" for f in QUERY_DATA_FIELD_LIST)
        + ")"
    )
    await conn.execute("CREATE TABLE redirect (名称 TEXT, 重定向 TEXT)")
    placeholder = ",".join("?" for _ in QUERY_DATA_FIELD_LIST)
    await conn.executemany(f"INSERT INTO data VALUES ({placeholder})", rows)
    await conn.commit()
    await conn.close()


# ── Q21: QueryStore 完整契约测试 ──────────────────────────────────────────


class TestQueryStoreConnect:
    """connect_path / has_database / list_databases / close_all"""

    @pytest.mark.asyncio
    async def test_connect_single_db(self, tmp_path):
        from core.data.query_store import QueryStore

        store = QueryStore()
        db_path = str(tmp_path / "test.db")
        await _create_test_db(db_path, [("foo", "", "src", "cat", "", "bar")])

        await store.connect_path(db_path)
        assert store.has_database("test") is True
        assert "test" in store.list_databases()

        await store.disconnect_database("test")
        assert store.has_database("test") is False

    @pytest.mark.asyncio
    async def test_connect_directory_recursively_loads_dbs(self, tmp_path):
        from core.data.query_store import QueryStore

        sub = tmp_path / "sub"
        sub.mkdir()
        db1 = str(sub / "a.db")
        db2 = str(sub / "b.db")
        await _create_test_db(db1, [])
        await _create_test_db(db2, [])

        store = QueryStore()
        await store.connect_path(str(tmp_path))
        assert store.has_database("a") is True
        assert store.has_database("b") is True

    @pytest.mark.asyncio
    async def test_connect_directory_skips_journal(self, tmp_path):
        from core.data.query_store import QueryStore

        db_path = str(tmp_path / "d.db")
        await _create_test_db(db_path, [])
        journal = str(tmp_path / "d.db-journal")
        with open(journal, "w") as f:
            f.write("junk")

        store = QueryStore()
        await store.connect_path(str(tmp_path))
        assert store.has_database("d") is True

    @pytest.mark.asyncio
    async def test_connect_nonexistent_path_creates_parent_dir(self, tmp_path):
        from core.data.query_store import QueryStore

        target = tmp_path / "new_dir"
        store = QueryStore()
        result = await store.connect_path(str(target))
        assert target.exists()

    @pytest.mark.asyncio
    async def test_close_all_disconnects_all(self, tmp_path):
        from core.data.query_store import QueryStore

        db1 = str(tmp_path / "x.db")
        db2 = str(tmp_path / "y.db")
        await _create_test_db(db1, [])
        await _create_test_db(db2, [])

        store = QueryStore()
        await store.connect_path(str(tmp_path))
        assert len(store.list_databases()) == 2

        await store.close_all()
        assert store.list_databases() == []


class TestQueryStoreExecutemany:
    """executemany / execute 方法"""

    @pytest.mark.asyncio
    async def test_executemany_inserts_and_commits(self, tmp_path):
        from core.data.query_store import QueryStore

        db_path = str(tmp_path / "d.db")
        await _create_test_db(db_path, [])
        store = QueryStore()
        await store.connect_path(db_path)

        await store.executemany(
            "d",
            "INSERT INTO data VALUES (?,?,?,?,?,?)",
            [("a1", "e1", "s1", "c1", "t1", "c1")],
            commit=True,
        )
        rows = await store.fetchall("d", "SELECT 名称 FROM data")
        assert len(rows) == 1
        assert rows[0][0] == "a1"

    @pytest.mark.asyncio
    async def test_execute_raises_on_unloaded_db(self, tmp_path):
        from core.data.query_store import QueryStore, QueryStoreError

        store = QueryStore()
        with pytest.raises(RuntimeError, match="not loaded"):
            await store.execute("nonexistent", "SELECT 1")


class TestQueryStoreSearch:
    """search 搜索契约"""

    @pytest.mark.asyncio
    async def test_search_single_db_returns_results(self, tmp_path):
        from core.data.query_store import QueryStore

        db_path = str(tmp_path / "d.db")
        await _create_test_db(db_path, [
            ("火球术", "Fireball", "PHB", "法术", "伤害", "3d6"),
        ])
        store = QueryStore()
        await store.connect_path(db_path)

        result = await store.search(["d"], ["火球术"])
        assert result["total"] == 1
        assert len(result["results"]) == 1
        assert result["results"][0]["name"] == "火球术"

    @pytest.mark.asyncio
    async def test_search_empty_tokens_returns_empty(self, tmp_path):
        from core.data.query_store import QueryStore

        db_path = str(tmp_path / "d.db")
        await _create_test_db(db_path, [("foo", "", "", "", "", "")])
        store = QueryStore()
        await store.connect_path(db_path)

        result = await store.search(["d"], [])
        assert result == {"results": [], "total": 0}

    @pytest.mark.asyncio
    async def test_search_raises_on_unloaded_db(self, tmp_path):
        from core.data.query_store import QueryStore, QueryStoreError

        store = QueryStore()
        with pytest.raises(QueryStoreError, match="未加载"):
            await store.search(["nonexistent"], ["foo"])

    @pytest.mark.asyncio
    async def test_search_pagination(self, tmp_path):
        from core.data.query_store import QueryStore

        db_path = str(tmp_path / "d.db")
        rows = [(f"name{i}", "", "src", "", "", f"content{i}") for i in range(10)]
        await _create_test_db(db_path, rows)
        store = QueryStore()
        await store.connect_path(db_path)

        result = await store.search(["d"], ["name"], limit=3, offset=2)
        assert len(result["results"]) == 3
        assert result["results"][0]["name"] == "name2"
        assert result["results"][1]["name"] == "name3"
        assert result["results"][2]["name"] == "name4"

    @pytest.mark.asyncio
    async def test_search_private_overrides_master(self, tmp_path):
        """私设库中的同名条目应覆盖主库条目。"""
        from core.data.query_store import QueryStore

        master = str(tmp_path / "master.db")
        private = str(tmp_path / "private.db")
        await _create_test_db(master, [("火球术", "Fireball", "PHB", "", "", "3d6")])
        await _create_test_db(private, [("火球术", "Fireball", "私设", "", "", "4d6")])

        store = QueryStore()
        await store.connect_path(str(tmp_path))

        result = await store.search(["master", "private"], ["火球术"])
        assert result["total"] == 1
        assert result["results"][0]["source"] == "私设"
        assert result["results"][0]["content"] == "4d6"

