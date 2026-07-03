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

    # Runtime data directory (respects DICEPP_DATA_DIR override)
    _data_dir_override = os.environ.get("DICEPP_DATA_DIR")
    DATA_ROOT = Path(_data_dir_override) if _data_dir_override else PROJECT_ROOT / "data"
    DATA_BOTS_DIR = DATA_ROOT / "bots"
    LOGS_DIR = DATA_ROOT / "logs"
    RUNTIME_LOG = LOGS_DIR / "dicepp-runtime.log"

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


@dataclass(frozen=True)
class ManagerRuntimeSettings:
    """Dashboard Manager runtime backend selection from environment variables."""

    runtime: str = "unavailable"
    process_command: str = ""
    process_cwd: str | None = None
    process_stop_timeout: float = 2.0
    docker_command: str = ""
    docker_service_template: str = ""
    docker_cwd: str | None = None
    docker_timeout: float = 10.0

    @classmethod
    def from_env(cls) -> "ManagerRuntimeSettings":
        runtime = os.environ.get("DICEPP_MANAGER_RUNTIME", "unavailable")
        normalized_runtime = runtime.strip().lower()
        if normalized_runtime == "process":
            stop_timeout_raw = os.environ.get("DICEPP_MANAGER_PROCESS_STOP_TIMEOUT", "2.0")
            try:
                stop_timeout = float(stop_timeout_raw)
            except ValueError as exc:
                raise ValueError(
                    "DICEPP_MANAGER_PROCESS_STOP_TIMEOUT must be a number"
                ) from exc
            return cls(
                runtime=runtime,
                process_command=os.environ.get("DICEPP_MANAGER_PROCESS_COMMAND", ""),
                process_cwd=os.environ.get(
                    "DICEPP_MANAGER_PROCESS_CWD",
                    str(DashboardPaths.PROJECT_ROOT),
                ),
                process_stop_timeout=stop_timeout,
            )
        if normalized_runtime == "docker-compose":
            timeout_raw = os.environ.get("DICEPP_MANAGER_DOCKER_TIMEOUT", "10.0")
            try:
                timeout = float(timeout_raw)
            except ValueError as exc:
                raise ValueError(
                    "DICEPP_MANAGER_DOCKER_TIMEOUT must be a number"
                ) from exc
            return cls(
                runtime=runtime,
                docker_command=os.environ.get("DICEPP_MANAGER_DOCKER_COMMAND", ""),
                docker_service_template=os.environ.get(
                    "DICEPP_MANAGER_DOCKER_SERVICE",
                    "",
                ),
                docker_cwd=os.environ.get("DICEPP_MANAGER_DOCKER_CWD"),
                docker_timeout=timeout,
            )
        return cls(runtime=runtime)
