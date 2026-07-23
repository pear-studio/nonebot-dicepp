"""Manager and Dashboard-client configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dicepp_data import InstanceLayout
from .models import validate_runtime_unit_id


def _float_env(name: str, default: float) -> float:
    try:
        value = float(os.environ.get(name, str(default)))
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc
    if value <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return value


def _bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean")


@dataclass(frozen=True, slots=True)
class ManagerSettings:
    layout: InstanceLayout
    host: str = "127.0.0.1"
    port: int = 4091
    runtime: str = "unavailable"
    runtime_unit_id: str = "dicepp-runtime"
    process_command: str = ""
    process_cwd: str | None = None
    process_stop_timeout: float = 2.0
    docker_command: str = "unix:///var/run/docker.sock"
    docker_timeout: float = 30.0
    token_path: Path | None = None
    release_scheduler_enabled: bool = True
    github_api: str = "https://api.github.com/repos/pear-studio/nonebot-dicepp"

    def __post_init__(self) -> None:
        validate_runtime_unit_id(self.runtime_unit_id)

    @classmethod
    def from_env(cls, default_root: str | os.PathLike[str]) -> "ManagerSettings":
        layout = InstanceLayout.from_env(default_root)
        return cls(
            layout=layout,
            host=os.environ.get("DICEPP_MANAGER_HOST", "127.0.0.1"),
            port=int(os.environ.get("DICEPP_MANAGER_PORT", "4091")),
            runtime=os.environ.get("DICEPP_MANAGER_RUNTIME", "unavailable").strip().lower(),
            runtime_unit_id=os.environ.get("DICEPP_MANAGER_RUNTIME_UNIT_ID", "dicepp-runtime"),
            process_command=os.environ.get("DICEPP_MANAGER_PROCESS_COMMAND", ""),
            process_cwd=os.environ.get("DICEPP_MANAGER_PROCESS_CWD", str(layout.root)),
            process_stop_timeout=_float_env("DICEPP_MANAGER_PROCESS_STOP_TIMEOUT", 2.0),
            docker_command=os.environ.get("DICEPP_MANAGER_DOCKER_COMMAND", "unix:///var/run/docker.sock"),
            docker_timeout=_float_env("DICEPP_MANAGER_DOCKER_TIMEOUT", 30.0),
            token_path=Path(os.environ.get("DICEPP_MANAGER_TOKEN_FILE", str(layout.manager_token))),
            release_scheduler_enabled=_bool_env(
                "DICEPP_MANAGER_RELEASE_SCHEDULER",
                True,
            ),
            github_api=os.environ.get(
                "DICEPP_GITHUB_API",
                "https://api.github.com/repos/pear-studio/nonebot-dicepp",
            ),
        )


@dataclass(frozen=True, slots=True)
class ManagerClientSettings:
    base_url: str
    token_path: Path
    timeout: float = 10.0

    @classmethod
    def from_layout(cls, layout: InstanceLayout) -> "ManagerClientSettings":
        return cls(
            base_url=os.environ.get("DICEPP_MANAGER_URL", "http://127.0.0.1:4091").rstrip("/"),
            token_path=Path(os.environ.get("DICEPP_MANAGER_TOKEN_FILE", str(layout.manager_token))),
            timeout=_float_env("DICEPP_MANAGER_CLIENT_TIMEOUT", 10.0),
        )
