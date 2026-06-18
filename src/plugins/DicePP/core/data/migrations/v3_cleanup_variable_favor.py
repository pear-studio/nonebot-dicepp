from __future__ import annotations

from .base import Migration, MigrationContext


class CleanupVariableFavorV3(Migration):
    def __init__(self) -> None:
        super().__init__(
            version=3,
            name="v3_cleanup_variable_favor",
            description="Drop unused variable and favor tables; migrate hub_config.value to data if needed.",
        )

    async def up(self, ctx: MigrationContext) -> None:
        # Drop dead tables (removed from schema — no code path reads/writes them)
        await ctx.db.execute("DROP TABLE IF EXISTS variable")
        await ctx.db.execute("DROP TABLE IF EXISTS favor")
        # For databases with the legacy v2 migration (column named "value"),
        # rename to "data" so hub_get/hub_set queries work.  New installs
        # already create the column as "data" — ignore the error in that case.
        try:
            await ctx.db.execute(
                "ALTER TABLE hub_config RENAME COLUMN value TO data"
            )
        except Exception:
            # Verify the column really is already "data" (new install).
            # Re-raise for real failures (disk I/O error, corruption, etc.)
            cursor = await ctx.db.execute("PRAGMA table_info(hub_config)")
            cols = {row[1] for row in await cursor.fetchall()}
            if "value" in cols and "data" not in cols:
                raise  # real failure: column still named "value"
            # else: column already "data", or table doesn't exist — safe
