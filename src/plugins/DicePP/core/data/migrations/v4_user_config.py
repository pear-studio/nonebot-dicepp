from __future__ import annotations

from .base import Migration, MigrationContext


class UserConfigMigrationV4(Migration):
    def __init__(self) -> None:
        super().__init__(
            version=4,
            name="v4_user_config",
            description="Add user_config table for per-user settings (e.g. chat_time).",
        )

    async def up(self, ctx: MigrationContext) -> None:
        await ctx.db.execute(
            """
            CREATE TABLE IF NOT EXISTS user_config (
                user_id TEXT,
                data TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (user_id)
            )
            """
        )
