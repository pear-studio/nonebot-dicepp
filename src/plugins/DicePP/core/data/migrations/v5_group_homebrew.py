from __future__ import annotations

from .base import Migration, MigrationContext


class GroupHomebrewMigrationV5(Migration):
    """添加 group_macro 表（群级宏；附加查询数据库走文件系统而非 BotDatabase）"""

    def __init__(self) -> None:
        super().__init__(
            version=5,
            name="v5_group_homebrew",
            description="Add group_macro table for group-level macros (homebrew).",
        )

    async def up(self, ctx: MigrationContext) -> None:
        await ctx.db.execute(
            """
            CREATE TABLE IF NOT EXISTS group_macro (
                group_id TEXT,
                key TEXT,
                data TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (group_id, key)
            )
            """
        )
