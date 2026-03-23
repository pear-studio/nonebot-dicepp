from __future__ import annotations

from .base import Migration, MigrationContext


class BaselineMigrationV1(Migration):
    def __init__(self) -> None:
        super().__init__(
            version=1,
            name="v1_baseline",
            description="Create baseline schema for all current business tables.",
        )

    async def up(self, ctx: MigrationContext) -> None:
        await ctx.db.execute(
            """
            CREATE TABLE IF NOT EXISTS karma (
                user_id TEXT,
                group_id TEXT,
                data TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (user_id, group_id)
            )
            """
        )
        await ctx.db.execute(
            """
            CREATE TABLE IF NOT EXISTS initiative (
                group_id TEXT,
                data TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (group_id)
            )
            """
        )
        await ctx.db.execute(
            """
            CREATE TABLE IF NOT EXISTS characters_dnd (
                group_id TEXT,
                user_id TEXT,
                data TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (group_id, user_id)
            )
            """
        )
        await ctx.db.execute(
            """
            CREATE TABLE IF NOT EXISTS nickname (
                user_id TEXT,
                group_id TEXT,
                data TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (user_id, group_id)
            )
            """
        )
        await ctx.db.execute(
            """
            CREATE TABLE IF NOT EXISTS group_config (
                group_id TEXT,
                data TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (group_id)
            )
            """
        )
        await ctx.db.execute(
            """
            CREATE TABLE IF NOT EXISTS group_activate (
                group_id TEXT,
                data TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (group_id)
            )
            """
        )
        await ctx.db.execute(
            """
            CREATE TABLE IF NOT EXISTS group_welcome (
                group_id TEXT,
                data TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (group_id)
            )
            """
        )
        await ctx.db.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_record (
                group_id TEXT,
                user_id TEXT,
                time TEXT,
                data TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (group_id, user_id, time)
            )
            """
        )
        await ctx.db.execute(
            """
            CREATE TABLE IF NOT EXISTS bot_control (
                key TEXT,
                data TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (key)
            )
            """
        )
        await ctx.db.execute(
            """
            CREATE TABLE IF NOT EXISTS user_stat (
                user_id TEXT,
                data TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (user_id)
            )
            """
        )
        await ctx.db.execute(
            """
            CREATE TABLE IF NOT EXISTS group_stat (
                group_id TEXT,
                data TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (group_id)
            )
            """
        )
        await ctx.db.execute(
            """
            CREATE TABLE IF NOT EXISTS meta_stat (
                key TEXT,
                data TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (key)
            )
            """
        )
        await ctx.db.execute(
            """
            CREATE TABLE IF NOT EXISTS npc_health (
                group_id TEXT,
                name TEXT,
                data TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (group_id, name)
            )
            """
        )
        await ctx.db.execute(
            """
            CREATE TABLE IF NOT EXISTS variable (
                user_id TEXT,
                group_id TEXT,
                name TEXT,
                data TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (user_id, group_id, name)
            )
            """
        )
        await ctx.db.execute(
            """
            CREATE TABLE IF NOT EXISTS favor (
                user_id TEXT,
                group_id TEXT,
                data TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (user_id, group_id)
            )
            """
        )

        await ctx.log_db.execute(
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
        await ctx.log_db.execute("CREATE INDEX IF NOT EXISTS idx_logs_group ON logs(group_id);")

        await ctx.log_db.execute(
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
        await ctx.log_db.execute("CREATE INDEX IF NOT EXISTS idx_records_log ON records(log_id);")
        await ctx.log_db.execute("CREATE INDEX IF NOT EXISTS idx_records_msg ON records(message_id);")
        await ctx.log_db.execute("CREATE INDEX IF NOT EXISTS idx_records_user_id_desc ON records(user_id, id DESC);")
        await ctx.log_db.execute("CREATE INDEX IF NOT EXISTS idx_records_log_id_desc ON records(log_id, id DESC);")
        await ctx.log_db.execute("CREATE INDEX IF NOT EXISTS idx_records_time ON records(time);")
