import aiosqlite
from datetime import datetime
from typing import List, Optional

from .models import LogSession, LogRecord


class LogRepository:
    def __init__(self, db: aiosqlite.Connection):
        self._db = db

    async def _ensure_table(self) -> None:
        await self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS logs (
                id TEXT PRIMARY KEY,
                group_id TEXT NOT NULL,
                name TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                recording INTEGER NOT NULL DEFAULT 0,
                record_begin_at TEXT NOT NULL,
                last_warn TEXT NOT NULL,
                filter_outside INTEGER NOT NULL DEFAULT 0,
                filter_command INTEGER NOT NULL DEFAULT 0,
                filter_bot INTEGER NOT NULL DEFAULT 0,
                filter_media INTEGER NOT NULL DEFAULT 0,
                filter_forum_code INTEGER NOT NULL DEFAULT 0,
                upload_time TEXT,
                upload_file TEXT,
                upload_note TEXT,
                url TEXT
            )
            """
        )
        await self._db.execute("CREATE INDEX IF NOT EXISTS idx_logs_group ON logs(group_id);")

        await self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                log_id TEXT NOT NULL,
                time TEXT NOT NULL,
                user_id TEXT NOT NULL,
                nickname TEXT,
                content TEXT NOT NULL,
                source TEXT NOT NULL,
                message_id TEXT,
                FOREIGN KEY(log_id) REFERENCES logs(id) ON DELETE CASCADE
            )
            """
        )
        await self._db.execute("CREATE INDEX IF NOT EXISTS idx_records_log ON records(log_id);")
        await self._db.execute("CREATE INDEX IF NOT EXISTS idx_records_msg ON records(message_id);")
        # 常见查询优化：按 group/user 拉取记录时的时间/倒序分页
        await self._db.execute("CREATE INDEX IF NOT EXISTS idx_records_user_id_desc ON records(user_id, id DESC);")
        await self._db.execute("CREATE INDEX IF NOT EXISTS idx_records_log_id_desc ON records(log_id, id DESC);")
        # 清理过期记录/按时间筛选
        await self._db.execute("CREATE INDEX IF NOT EXISTS idx_records_time ON records(time);")
        await self._db.commit()

    async def get_session(self, log_id: str) -> Optional[LogSession]:
        cursor = await self._db.execute(
            "SELECT * FROM logs WHERE id = ?",
            (log_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None

        return LogSession(
            id=row[0],
            group_id=row[1],
            name=row[2],
            created_at=datetime.fromisoformat(row[3]),
            updated_at=datetime.fromisoformat(row[4]),
            recording=bool(row[5]),
            record_begin_at=row[6],
            last_warn=row[7],
            filter_outside=bool(row[8]),
            filter_command=bool(row[9]),
            filter_bot=bool(row[10]),
            filter_media=bool(row[11]),
            filter_forum_code=bool(row[12]),
            upload_time=row[13],
            upload_file=row[14],
            upload_note=row[15],
            url=row[16],
        )

    async def save_session(self, session: LogSession) -> None:
        await self._db.execute(
            """
            INSERT INTO logs (
                id, group_id, name, created_at, updated_at, recording, record_begin_at, last_warn,
                filter_outside, filter_command, filter_bot, filter_media, filter_forum_code,
                upload_time, upload_file, upload_note, url
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                group_id=excluded.group_id,
                name=excluded.name,
                created_at=excluded.created_at,
                updated_at=excluded.updated_at,
                recording=excluded.recording,
                record_begin_at=excluded.record_begin_at,
                last_warn=excluded.last_warn,
                filter_outside=excluded.filter_outside,
                filter_command=excluded.filter_command,
                filter_bot=excluded.filter_bot,
                filter_media=excluded.filter_media,
                filter_forum_code=excluded.filter_forum_code,
                upload_time=excluded.upload_time,
                upload_file=excluded.upload_file,
                upload_note=excluded.upload_note,
                url=excluded.url
            """,
            (
                session.id,
                session.group_id,
                session.name,
                session.created_at.isoformat(),
                session.updated_at.isoformat(),
                int(session.recording),
                session.record_begin_at,
                session.last_warn,
                int(session.filter_outside),
                int(session.filter_command),
                int(session.filter_bot),
                int(session.filter_media),
                int(session.filter_forum_code),
                session.upload_time,
                session.upload_file,
                session.upload_note,
                session.url,
            ),
        )
        await self._db.commit()

    async def add_record(self, record: LogRecord) -> int:
        cursor = await self._db.execute(
            "INSERT INTO records (log_id, time, user_id, nickname, content, source, message_id) VALUES (?,?,?,?,?,?,?)",
            (
                record.log_id,
                record.time.isoformat() if isinstance(record.time, datetime) else record.time,
                record.user_id,
                record.nickname,
                record.content,
                record.source,
                record.message_id,
            ),
        )
        await self._db.commit()
        return cursor.lastrowid

    async def get_records(self, log_id: str) -> List[LogRecord]:
        cursor = await self._db.execute(
            "SELECT id, log_id, time, user_id, nickname, content, source, message_id FROM records WHERE log_id=? ORDER BY id ASC",
            (log_id,),
        )
        rows = await cursor.fetchall()
        return [
            LogRecord(
                id=row[0],
                log_id=row[1],
                time=datetime.fromisoformat(row[2]),
                user_id=row[3],
                nickname=row[4] or "",
                content=row[5],
                source=row[6],
                message_id=row[7],
            )
            for row in rows
        ]

    async def delete_session(self, log_id: str) -> bool:
        """删除会话及其所有记录（外键 CASCADE 自动处理 records）"""
        cursor = await self._db.execute("DELETE FROM logs WHERE id = ?", (log_id,))
        await self._db.commit()
        return cursor.rowcount > 0

    async def delete_records_by_message(self, log_id: str, message_id: str) -> int:
        cursor = await self._db.execute(
            "DELETE FROM records WHERE log_id=? AND message_id=?",
            (log_id, message_id),
        )
        await self._db.commit()
        return cursor.rowcount

    async def insert(self, record: LogRecord) -> int:
        cursor = await self._db.execute(
            "INSERT INTO records (log_id, time, user_id, nickname, content, source, message_id) VALUES (?,?,?,?,?,?,?)",
            (
                record.log_id,
                record.time.isoformat() if isinstance(record.time, datetime) else record.time,
                record.user_id,
                record.nickname,
                record.content,
                record.source,
                record.message_id,
            ),
        )
        await self._db.commit()
        return cursor.lastrowid

    async def query_by_group(self, group_id: str, limit: int = 100) -> List[LogRecord]:
        cursor = await self._db.execute(
            """
            SELECT r.id, r.log_id, r.time, r.user_id, r.nickname, r.content, r.source, r.message_id
            FROM records r
            JOIN logs l ON r.log_id = l.id
            WHERE l.group_id = ?
            ORDER BY r.id DESC
            LIMIT ?
            """,
            (group_id, limit),
        )
        rows = await cursor.fetchall()
        return [
            LogRecord(
                id=row[0],
                log_id=row[1],
                time=datetime.fromisoformat(row[2]),
                user_id=row[3],
                nickname=row[4] or "",
                content=row[5],
                source=row[6],
                message_id=row[7],
            )
            for row in rows
        ]

    async def query_by_user(self, user_id: str, limit: int = 50) -> List[LogRecord]:
        cursor = await self._db.execute(
            """
            SELECT id, log_id, time, user_id, nickname, content, source, message_id
            FROM records
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (user_id, limit),
        )
        rows = await cursor.fetchall()
        return [
            LogRecord(
                id=row[0],
                log_id=row[1],
                time=datetime.fromisoformat(row[2]),
                user_id=row[3],
                nickname=row[4] or "",
                content=row[5],
                source=row[6],
                message_id=row[7],
            )
            for row in rows
        ]

    async def delete_before(self, timestamp: datetime) -> int:
        cursor = await self._db.execute(
            "DELETE FROM records WHERE time < ?",
            (timestamp.isoformat(),),
        )
        await self._db.commit()
        return cursor.rowcount
