from __future__ import annotations

from .base import Migration, MigrationContext


class HubConfigMigrationV2(Migration):
    def __init__(self) -> None:
        super().__init__(
            version=2,
            name="v2_hub_config",
            description="Add hub_config table for DiceHub settings.",
        )

    async def up(self, ctx: MigrationContext) -> None:
        await ctx.db.execute(
            """
            CREATE TABLE IF NOT EXISTS hub_config (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )

