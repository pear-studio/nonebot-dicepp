from __future__ import annotations

from .base import Migration, MigrationContext


class GroupTeamMigrationV4(Migration):
    """添加 group_team 表（跑团队伍管理）"""

    def __init__(self) -> None:
        super().__init__(
            version=4,
            name="v4_group_team",
            description="Add group_team table for .team commands.",
        )

    async def up(self, ctx: MigrationContext) -> None:
        await ctx.db.execute(
            """
            CREATE TABLE IF NOT EXISTS group_team (
                group_id TEXT PRIMARY KEY,
                data TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
