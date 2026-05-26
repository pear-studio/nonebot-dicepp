from __future__ import annotations

from .base import Migration, MigrationContext


class MacrosPointsMigrationV3(Migration):
    """添加 macros 和 point 表（β 业务移植）"""

    def __init__(self) -> None:
        super().__init__(
            version=3,
            name="v3_macros_points",
            description="Add macros and point tables (ported from β).",
        )

    async def up(self, ctx: MigrationContext) -> None:
        await ctx.db.execute(
            """
            CREATE TABLE IF NOT EXISTS macros (
                user_id TEXT,
                key TEXT,
                data TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (user_id, key)
            )
            """
        )
        await ctx.db.execute(
            """
            CREATE TABLE IF NOT EXISTS point (
                user_id TEXT,
                data TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (user_id)
            )
            """
        )
