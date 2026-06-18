import os
from pathlib import Path
from dataclasses import dataclass


class DashboardPaths:
    """Path resolution for DicePP dashboard."""

    # Auto-detect project root: go up 3 levels from this file
    # dashboard/src/config.py -> dashboard/src/ -> dashboard/ -> project root
    PROJECT_ROOT = Path(os.environ.get(
        "DICEPP_PROJECT_ROOT",
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    ))

    # Dashboard data directory (alongside dashboard package dir)
    DATA_DIR = PROJECT_ROOT / "dashboard" / "data"
    DASHBOARD_DB = DATA_DIR / "dashboard.db"

    # Config paths (relative to project root)
    CONFIG_DIR = PROJECT_ROOT / "config"
    CONFIG_GLOBAL = CONFIG_DIR / "global.json"
    CONFIG_USER = CONFIG_DIR / "user.json"
    CONFIG_BOTS_DIR = CONFIG_DIR / "bots"
    CONFIG_SCHEMA = CONFIG_DIR / "schema.json"

    # Bot data directory (respects DICEPP_DATA_DIR override)
    _data_dir_override = os.environ.get("DICEPP_DATA_DIR")
    DATA_BOTS_DIR = Path(_data_dir_override) / "bots" if _data_dir_override else PROJECT_ROOT / "data" / "bots"

    # Content directory
    CONTENT_DIR = PROJECT_ROOT / "content"

    @classmethod
    def bot_data_db_path(cls, bot_id: str) -> Path:
        """Resolve bot_data.db path.

        If DICEPP_DATA_DIR is set, use that + bots/{bot_id}/bot_data.db.
        Otherwise, use PROJECT_ROOT/data/bots/{bot_id}/bot_data.db.
        """
        override = os.environ.get("DICEPP_DATA_DIR")
        if override:
            return Path(override) / "bots" / bot_id / "bot_data.db"
        return cls.PROJECT_ROOT / "data" / "bots" / bot_id / "bot_data.db"

    @classmethod
    def bot_config_path(cls, bot_id: str) -> Path:
        """Resolve config/bots/{bot_id}.json path."""
        return cls.CONFIG_BOTS_DIR / f"{bot_id}.json"


@dataclass
class DashboardSettings:
    """Dashboard server settings from environment variables."""
    host: str = os.environ.get("DASHBOARD_HOST", "0.0.0.0")
    port: int = int(os.environ.get("DASHBOARD_PORT", "4090"))
