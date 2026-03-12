import pytest
import asyncio
import os
import tempfile
from typing import List

from plugins.DicePP.core.data import Repository
from plugins.DicePP.core.data.models import UserKarma


class TestRepository:
    @pytest.fixture
    async def repo(self):
        import aiosqlite

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            db = await aiosqlite.connect(db_path)
            repo = Repository[UserKarma](db, UserKarma, "karma", ["user_id", "group_id"])
            await repo._ensure_table()
            yield repo
            await db.close()

    @pytest.mark.asyncio
    async def test_save_and_get(self, repo):
        karma = UserKarma(user_id="user1", group_id="group1", value=50)
        await repo.save(karma)

        result = await repo.get("user1", "group1")
        assert result is not None
        assert result.user_id == "user1"
        assert result.group_id == "group1"
        assert result.value == 50

    @pytest.mark.asyncio
    async def test_get_not_exists(self, repo):
        result = await repo.get("not_exists", "group1")
        assert result is None

    @pytest.mark.asyncio
    async def test_update(self, repo):
        karma1 = UserKarma(user_id="user1", group_id="group1", value=50)
        await repo.save(karma1)

        karma2 = UserKarma(user_id="user1", group_id="group1", value=100)
        await repo.save(karma2)

        result = await repo.get("user1", "group1")
        assert result.value == 100

    @pytest.mark.asyncio
    async def test_delete(self, repo):
        karma = UserKarma(user_id="user1", group_id="group1", value=50)
        await repo.save(karma)

        deleted = await repo.delete("user1", "group1")
        assert deleted is True

        result = await repo.get("user1", "group1")
        assert result is None

    @pytest.mark.asyncio
    async def test_delete_not_exists(self, repo):
        deleted = await repo.delete("not_exists", "group1")
        assert deleted is False

    @pytest.mark.asyncio
    async def test_list_all(self, repo):
        await repo.save(UserKarma(user_id="user1", group_id="group1", value=10))
        await repo.save(UserKarma(user_id="user2", group_id="group1", value=20))
        await repo.save(UserKarma(user_id="user3", group_id="group1", value=30))

        results = await repo.list_all()
        assert len(results) == 3

    @pytest.mark.asyncio
    async def test_list_by(self, repo):
        await repo.save(UserKarma(user_id="user1", group_id="group1", value=10))
        await repo.save(UserKarma(user_id="user2", group_id="group1", value=20))
        await repo.save(UserKarma(user_id="user3", group_id="group2", value=30))

        results = await repo.list_by(group_id="group1")
        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_list_by_no_filter(self, repo):
        await repo.save(UserKarma(user_id="user1", group_id="group1", value=10))
        await repo.save(UserKarma(user_id="user2", group_id="group1", value=20))

        results = await repo.list_by()
        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_wrong_key_count(self, repo):
        with pytest.raises(ValueError):
            await repo.get("user1")

        with pytest.raises(ValueError):
            await repo.delete("user1", "group1", "extra")
