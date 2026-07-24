"""Small Manager-client fakes shared by Dashboard integration tests."""

from __future__ import annotations

import json

from dashboard.src.config import DashboardPaths


class PersistingConfigManager:
    """Emulate successful Manager-owned config persistence for Dashboard tests."""

    async def save_user_config(self, config: dict) -> dict:
        DashboardPaths.CONFIG_USER.write_text(
            json.dumps(config, ensure_ascii=False),
            encoding="utf-8",
        )
        return {"saved": True}

    async def save_bot_config(self, bot_id: str, config: dict) -> dict:
        DashboardPaths.bot_config_path(bot_id).write_text(
            json.dumps(config, ensure_ascii=False),
            encoding="utf-8",
        )
        return {"saved": True}
