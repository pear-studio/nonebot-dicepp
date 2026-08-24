"""Small Manager-client fakes shared by Dashboard integration tests."""

from __future__ import annotations

from dashboard.src.config import DashboardPaths


class PersistingConfigManager:
    """Emulate successful Manager-owned config persistence for Dashboard tests."""

    async def control_bots(self) -> list[dict]:
        return []
