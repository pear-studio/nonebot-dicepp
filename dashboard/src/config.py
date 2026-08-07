import os
from pathlib import Path
from dataclasses import dataclass

from dicepp_data import BOT_CORE_ASSET, InstanceLayout


_SOURCE_ROOT = Path(__file__).resolve().parents[2]
_INSTANCE_LAYOUT = InstanceLayout.from_env(_SOURCE_ROOT)


class DashboardPaths:
    """Compatibility facade over the shared :class:`InstanceLayout`."""

    PROJECT_ROOT = _INSTANCE_LAYOUT.root

    # Dashboard install location / real project root — the directory the
    # dashboard package lives in, regardless of the runtime workspace that
    # PROJECT_ROOT may point at. Used for locating source files (e.g.
    # pydantic_models.py) that travel with the package, not the workspace.
    SOURCE_ROOT = _SOURCE_ROOT

    # Dashboard data directory (alongside dashboard package dir)
    DATA_DIR = _INSTANCE_LAYOUT.dashboard_data_dir
    DASHBOARD_DB = _INSTANCE_LAYOUT.dashboard_db

    # Config paths (relative to project root)
    CONFIG_DIR = _INSTANCE_LAYOUT.config_dir
    CONFIG_USER = _INSTANCE_LAYOUT.config_user
    CONFIG_BOTS_DIR = _INSTANCE_LAYOUT.config_bots_dir

    # Runtime data directory (respects DICEPP_DATA_DIR override)
    DATA_ROOT = _INSTANCE_LAYOUT.data_root
    DATA_BOTS_DIR = _INSTANCE_LAYOUT.data_bots_dir
    LOGS_DIR = _INSTANCE_LAYOUT.logs_dir
    RUNTIME_LOG = _INSTANCE_LAYOUT.runtime_log

    # Content directory
    CONTENT_DIR = _INSTANCE_LAYOUT.content_dir

    @classmethod
    def instance_layout(cls) -> InstanceLayout:
        return InstanceLayout.from_legacy_paths(cls)

    @classmethod
    def bot_data_db_path(cls, bot_id: str) -> Path:
        """Resolve bot_data.db path.

        If DICEPP_DATA_DIR is set, use that + bots/{bot_id}/bot_data.db.
        Otherwise, use PROJECT_ROOT/data/bots/{bot_id}/bot_data.db.
        """
        return BOT_CORE_ASSET.resolve(cls.instance_layout(), bot_id=bot_id)

    @classmethod
    def bot_config_path(cls, bot_id: str) -> Path:
        """Resolve config/bots/{bot_id}.json path."""
        return cls.instance_layout().bot_config_path(bot_id)

    @classmethod
    def runtime_log_path(cls) -> Path:
        """Resolve the shared Windows launcher/runtime log path."""
        override = os.environ.get("DICEPP_RUNTIME_LOG")
        if override:
            return Path(override)
        data_root = os.environ.get("DICEPP_DATA_DIR")
        if data_root:
            return Path(data_root) / "logs" / "dicepp-runtime.log"
        return cls.RUNTIME_LOG


@dataclass
class DashboardSettings:
    """Dashboard server settings from environment variables.

    Resolved lazily at instance creation time, not at import time.
    """
    host: str = "0.0.0.0"
    port: int = 4090

    def __post_init__(self) -> None:
        """Override defaults from environment variables if set."""
        self.host = os.environ.get("DASHBOARD_HOST", self.host)
        self.port = int(os.environ.get("DASHBOARD_PORT", str(self.port)))
