import pytest
import os
import tempfile
from datetime import datetime

from plugins.DicePP.core.data import LogRepository
from plugins.DicePP.core.data.models import LogSession, LogRecord


class TestLogRepository:
    @pytest.fixture
    async def log_repo(self):
        import aiosqlite

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "log.db")
            db = await aiosqlite.connect(db_path)
            # 启用外键约束，确保 CASCADE 生效
            await db.execute("PRAGMA foreign_keys=ON;")
            repo = LogRepository(db)
            await repo._ensure_table()
            yield repo
            await db.close()

    @pytest.mark.asyncio
    async def test_save_and_get_session(self, log_repo):
        session = LogSession(
            id="session1",
            group_id="group1",
            name="Test Session",
            recording=True,
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )
        await log_repo.save_session(session)

        result = await log_repo.get_session("session1")
        assert result is not None
        assert result.id == "session1"
        assert result.group_id == "group1"
        assert result.name == "Test Session"
        assert result.recording is True

    @pytest.mark.asyncio
    async def test_get_session_not_exists(self, log_repo):
        result = await log_repo.get_session("not_exists")
        assert result is None

    @pytest.mark.asyncio
    async def test_update_session(self, log_repo):
        session1 = LogSession(
            id="session1",
            group_id="group1",
            name="Original",
            recording=False,
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )
        await log_repo.save_session(session1)

        session2 = LogSession(
            id="session1",
            group_id="group1",
            name="Updated",
            recording=True,
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )
        await log_repo.save_session(session2)

        result = await log_repo.get_session("session1")
        assert result.name == "Updated"
        assert result.recording is True

    @pytest.mark.asyncio
    async def test_add_record(self, log_repo):
        session = LogSession(
            id="session1",
            group_id="group1",
            name="Test",
            recording=True,
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )
        await log_repo.save_session(session)

        record = LogRecord(
            log_id="session1",
            time=datetime.now(),
            user_id="user1",
            nickname="User One",
            content="Hello world",
            source="user",
        )
        record_id = await log_repo.add_record(record)
        assert record_id > 0

    @pytest.mark.asyncio
    async def test_get_records(self, log_repo):
        session = LogSession(
            id="session1",
            group_id="group1",
            name="Test",
            recording=True,
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )
        await log_repo.save_session(session)

        await log_repo.add_record(LogRecord(
            log_id="session1",
            time=datetime.now(),
            user_id="user1",
            nickname="User One",
            content="Hello",
            source="user",
        ))
        await log_repo.add_record(LogRecord(
            log_id="session1",
            time=datetime.now(),
            user_id="user2",
            nickname="User Two",
            content="Hi there",
            source="user",
        ))

        records = await log_repo.get_records("session1")
        assert len(records) == 2

    @pytest.mark.asyncio
    async def test_delete_session(self, log_repo):
        session = LogSession(
            id="session1",
            group_id="group1",
            name="Test",
            recording=True,
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )
        await log_repo.save_session(session)

        await log_repo.add_record(LogRecord(
            log_id="session1",
            time=datetime.now(),
            user_id="user1",
            nickname="User One",
            content="Hello",
            source="user",
        ))

        deleted = await log_repo.delete_session("session1")
        assert deleted is True

        result = await log_repo.get_session("session1")
        assert result is None

        records = await log_repo.get_records("session1")
        assert len(records) == 0

    @pytest.mark.asyncio
    async def test_delete_records_by_message(self, log_repo):
        session = LogSession(
            id="session1",
            group_id="group1",
            name="Test",
            recording=True,
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )
        await log_repo.save_session(session)

        await log_repo.add_record(LogRecord(
            log_id="session1",
            time=datetime.now(),
            user_id="user1",
            nickname="User One",
            content="Hello",
            source="user",
            message_id="msg1",
        ))

        deleted = await log_repo.delete_records_by_message("session1", "msg1")
        assert deleted == 1
