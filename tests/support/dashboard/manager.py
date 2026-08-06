"""Small Manager-client fakes shared by Dashboard integration tests."""

from __future__ import annotations

import json

from dashboard.src.config import DashboardPaths


class PersistingConfigManager:
    """Emulate successful Manager-owned config persistence for Dashboard tests."""

    async def get_user_config(self) -> dict:
        if not DashboardPaths.CONFIG_USER.exists():
            return {}
        return json.loads(DashboardPaths.CONFIG_USER.read_text(encoding="utf-8"))

    async def get_bot_config(self, bot_id: str) -> dict:
        path = DashboardPaths.bot_config_path(bot_id)
        if not path.exists():
            from dicepp_manager.client import ManagerClientError

            raise ManagerClientError(
                "Bot configuration not found",
                status_code=404,
                payload={"ok": False, "message": "Bot configuration not found"},
            )
        return json.loads(path.read_text(encoding="utf-8"))

    async def save_user_config(self, config: dict) -> dict:
        DashboardPaths.CONFIG_USER.write_text(
            json.dumps(config, ensure_ascii=False),
            encoding="utf-8",
        )
        return {
            "saved": True,
            "application": "deferred",
            "restart_required": True,
        }

    async def save_bot_config(self, bot_id: str, config: dict) -> dict:
        DashboardPaths.bot_config_path(bot_id).write_text(
            json.dumps(config, ensure_ascii=False),
            encoding="utf-8",
        )
        return {
            "saved": True,
            "application": "deferred",
            "restart_required": True,
        }

    async def control_bots(self) -> list[dict]:
        return []
