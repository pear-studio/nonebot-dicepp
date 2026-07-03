import pytest
import os
import tempfile
from datetime import datetime

pytestmark = pytest.mark.integration

from core.data import LogRepository
from core.data.models import LogSession, LogRecord


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
            created_at=datetime(2026, 5, 27, 12, 0, 0),
            updated_at=datetime(2026, 5, 27, 12, 0, 0),
        )
        await log_repo.save_session(session)

        result = await log_repo.get_session("session1")
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
            created_at=datetime(2026, 5, 27, 12, 0, 0),
            updated_at=datetime(2026, 5, 27, 12, 0, 0),
        )
        await log_repo.save_session(session1)

        session2 = LogSession(
            id="session1",
            group_id="group1",
            name="Updated",
            recording=True,
            created_at=datetime(2026, 5, 27, 12, 0, 0),
            updated_at=datetime(2026, 5, 27, 12, 0, 0),
        )
        await log_repo.save_session(session2)

        result = await log_repo.get_session("session1")
        assert result.name == "Updated"
        assert result.recording is True

    @pytest.mark.asyncio
    async def test_get_records(self, log_repo):
        session = LogSession(
            id="session1",
            group_id="group1",
            name="Test",
            recording=True,
            created_at=datetime(2026, 5, 27, 12, 0, 0),
            updated_at=datetime(2026, 5, 27, 12, 0, 0),
        )
        await log_repo.save_session(session)

        await log_repo.add_record(LogRecord(
            log_id="session1",
            time=datetime(2026, 5, 27, 12, 0, 0),
            user_id="user1",
            nickname="User One",
            content="Hello",
            source="user",
        ))
        await log_repo.add_record(LogRecord(
            log_id="session1",
            time=datetime(2026, 5, 27, 12, 0, 0),
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
            created_at=datetime(2026, 5, 27, 12, 0, 0),
            updated_at=datetime(2026, 5, 27, 12, 0, 0),
        )
        await log_repo.save_session(session)

        await log_repo.add_record(LogRecord(
            log_id="session1",
            time=datetime(2026, 5, 27, 12, 0, 0),
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
            created_at=datetime(2026, 5, 27, 12, 0, 0),
            updated_at=datetime(2026, 5, 27, 12, 0, 0),
        )
        await log_repo.save_session(session)

        await log_repo.add_record(LogRecord(
            log_id="session1",
            time=datetime(2026, 5, 27, 12, 0, 0),
            user_id="user1",
            nickname="User One",
            content="Hello",
            source="user",
            message_id="msg1",
        ))

        deleted = await log_repo.delete_records_by_message("session1", "msg1")
        assert deleted == 1

    @pytest.mark.asyncio
    async def test_add_record(self, log_repo):
        session = LogSession(
            id="session1",
            group_id="group1",
            name="Test",
            recording=True,
            created_at=datetime(2026, 5, 27, 12, 0, 0),
            updated_at=datetime(2026, 5, 27, 12, 0, 0),
        )
        await log_repo.save_session(session)

        record = LogRecord(
            log_id="session1",
            time=datetime(2026, 5, 27, 12, 0, 0),
            user_id="user1",
            nickname="User One",
            content="Test message",
            source="user",
        )
        record_id = await log_repo.insert(record)
        assert record_id > 0

        # Verify retrievable
        records = await log_repo.get_records("session1")
        assert len(records) >= 1
        assert records[-1].content == "Test message"

    @pytest.mark.asyncio
    async def test_query_by_group(self, log_repo):
        session1 = LogSession(
            id="session1",
            group_id="group1",
            name="Test Group 1",
            recording=True,
            created_at=datetime(2026, 5, 27, 12, 0, 0),
            updated_at=datetime(2026, 5, 27, 12, 0, 0),
        )
        session2 = LogSession(
            id="session2",
            group_id="group2",
            name="Test Group 2",
            recording=True,
            created_at=datetime(2026, 5, 27, 12, 0, 0),
            updated_at=datetime(2026, 5, 27, 12, 0, 0),
        )
        await log_repo.save_session(session1)
        await log_repo.save_session(session2)

        await log_repo.add_record(LogRecord(
            log_id="session1",
            time=datetime(2026, 5, 27, 12, 0, 0),
            user_id="user1",
            nickname="User One",
            content="Hello from group1",
            source="user",
        ))
        await log_repo.add_record(LogRecord(
            log_id="session2",
            time=datetime(2026, 5, 27, 12, 0, 0),
            user_id="user2",
            nickname="User Two",
            content="Hello from group2",
            source="user",
        ))

        records = await log_repo.query_by_group("group1", limit=10)
        assert len(records) >= 1
        assert records[0].content == "Hello from group1"

    @pytest.mark.asyncio
    async def test_query_by_user(self, log_repo):
        session = LogSession(
            id="session1",
            group_id="group1",
            name="Test",
            recording=True,
            created_at=datetime(2026, 5, 27, 12, 0, 0),
            updated_at=datetime(2026, 5, 27, 12, 0, 0),
        )
        await log_repo.save_session(session)

        await log_repo.add_record(LogRecord(
            log_id="session1",
            time=datetime(2026, 5, 27, 12, 0, 0),
            user_id="user1",
            nickname="User One",
            content="Message from user1",
            source="user",
        ))
        await log_repo.add_record(LogRecord(
            log_id="session1",
            time=datetime(2026, 5, 27, 12, 0, 0),
            user_id="user2",
            nickname="User Two",
            content="Message from user2",
            source="user",
        ))

        records = await log_repo.query_by_user("user1", limit=10)
        assert len(records) >= 1
        assert records[0].user_id == "user1"
        assert records[0].content == "Message from user1"

    @pytest.mark.asyncio
    async def test_delete_before(self, log_repo):
        session = LogSession(
            id="session1",
            group_id="group1",
            name="Test",
            recording=True,
            created_at=datetime(2026, 5, 27, 12, 0, 0),
            updated_at=datetime(2026, 5, 27, 12, 0, 0),
        )
        await log_repo.save_session(session)

        old_time = datetime(2020, 1, 1)
        new_time = datetime(2026, 5, 27, 12, 0, 0)

        await log_repo.insert(LogRecord(
            log_id="session1",
            time=old_time,
            user_id="user1",
            nickname="User One",
            content="Old message",
            source="user",
        ))
        await log_repo.insert(LogRecord(
            log_id="session1",
            time=new_time,
            user_id="user1",
            nickname="User One",
            content="New message",
            source="user",
        ))

        deleted_count = await log_repo.delete_before(datetime(2023, 1, 1))
        assert deleted_count == 1

        records = await log_repo.query_by_user("user1", limit=10)
        assert len(records) >= 1

    # ── Contract: 完整 CRUD 契约 ──────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_all_session_fields_roundtrip(self, log_repo):
        """LogSession 全部 17 个字段 save/get 完整回环"""
        dt = datetime(2026, 6, 1, 10, 30, 0)
        session = LogSession(
            id="full-session",
            group_id="group-full",
            name="Full Field Test",
            recording=True,
            created_at=dt,
            updated_at=dt,
            record_begin_at="2026-06-01T10:30:00",
            last_warn="2026-06-01T10:00:00",
            filter_outside=True,
            filter_command=False,
            filter_bot=True,
            filter_media=False,
            filter_forum_code=True,
            upload_time="2026-06-01T12:00:00",
            upload_file="log_export.json",
            upload_note="测试上传",
            url="https://example.com/log",
        )
        await log_repo.save_session(session)

        result = await log_repo.get_session("full-session")
        assert result.id == "full-session"
        assert result.group_id == "group-full"
        assert result.name == "Full Field Test"
        assert result.recording is True
        assert result.created_at == dt
        assert result.updated_at == dt
        assert result.record_begin_at == "2026-06-01T10:30:00"
        assert result.last_warn == "2026-06-01T10:00:00"
        assert result.filter_outside is True
        assert result.filter_command is False
        assert result.filter_bot is True
        assert result.filter_media is False
        assert result.filter_forum_code is True
        assert result.upload_time == "2026-06-01T12:00:00"
        assert result.upload_file == "log_export.json"
        assert result.upload_note == "测试上传"
        assert result.url == "https://example.com/log"

    @pytest.mark.asyncio
    async def test_session_optional_fields_none_roundtrip(self, log_repo):
        """upload_time / upload_file / upload_note / url 为 None 时的回环"""
        dt = datetime(2026, 6, 1, 10, 30, 0)
        session = LogSession(
            id="none-session",
            group_id="group-none",
            name="None Fields",
            recording=False,
            created_at=dt,
            updated_at=dt,
        )
        await log_repo.save_session(session)
        result = await log_repo.get_session("none-session")
        assert result.upload_time is None
        assert result.upload_file is None
        assert result.upload_note is None
        assert result.url is None
        assert result.recording is False

    @pytest.mark.asyncio
    async def test_delete_session_non_existent_returns_false(self, log_repo):
        """删除不存在的 session 返回 False"""
        result = await log_repo.delete_session("not_exists")
        assert result is False

    @pytest.mark.asyncio
    async def test_delete_records_by_message_non_existent_returns_zero(self, log_repo):
        """删除不存在的 message_id 返回 0"""
        session = LogSession(
            id="del-msg-session",
            group_id="g1",
            name="Delete Msg",
            recording=True,
            created_at=datetime(2026, 6, 1, 12, 0, 0),
            updated_at=datetime(2026, 6, 1, 12, 0, 0),
        )
        await log_repo.save_session(session)
        count = await log_repo.delete_records_by_message("del-msg-session", "nonexistent_msg")
        assert count == 0

    @pytest.mark.asyncio
    async def test_boolean_filter_fields_roundtrip(self, log_repo):
        """所有 boolean filter 字段独立回环"""
        dt = datetime(2026, 6, 1, 12, 0, 0)
        session = LogSession(
            id="filter-session",
            group_id="g1",
            name="Filters",
            recording=False,
            created_at=dt,
            updated_at=dt,
            filter_outside=False,
            filter_command=True,
            filter_bot=False,
            filter_media=True,
            filter_forum_code=False,
        )
        await log_repo.save_session(session)
        result = await log_repo.get_session("filter-session")
        assert result.filter_outside is False
        assert result.filter_command is True
        assert result.filter_bot is False
        assert result.filter_media is True
        assert result.filter_forum_code is False

    @pytest.mark.asyncio
    async def test_log_record_with_message_id_none(self, log_repo):
        """LogRecord 的 message_id 为 None 时可正常写入和读取"""
        session = LogSession(
            id="rec-none-msg",
            group_id="g1",
            name="Record None MsgId",
            recording=True,
            created_at=datetime(2026, 6, 1, 12, 0, 0),
            updated_at=datetime(2026, 6, 1, 12, 0, 0),
        )
        await log_repo.save_session(session)

        record = LogRecord(
            log_id="rec-none-msg",
            time=datetime(2026, 6, 1, 12, 30, 0),
            user_id="u1",
            nickname="",
            content="test",
            source="user",
            message_id=None,
        )
        record_id = await log_repo.add_record(record)
        assert record_id > 0

        records = await log_repo.get_records("rec-none-msg")
        assert len(records) == 1
        assert records[0].message_id is None
