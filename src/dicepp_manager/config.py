"""Manager and Dashboard-client configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dicepp_data import InstanceLayout
from .deployment import MANAGER_DEFAULT_PORT


def _float_env(name: str, default: float) -> float:
    try:
        value = float(os.environ.get(name, str(default)))
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc
    if value <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return value


@dataclass(frozen=True, slots=True)
class ManagerSettings:
    layout: InstanceLayout
    host: str = "127.0.0.1"
    port: int = MANAGER_DEFAULT_PORT
    control_heartbeat_timeout: float = 120.0
    control_reload_timeout: float = 5.0
    token_path: Path | None = None

    @classmethod
    def from_env(cls, default_root: str | os.PathLike[str]) -> "ManagerSettings":
        layout = InstanceLayout.from_env(default_root)
        return cls(
            layout=layout,
            host=os.environ.get("DICEPP_MANAGER_HOST", "127.0.0.1"),
            port=int(os.environ.get("DICEPP_MANAGER_PORT", str(MANAGER_DEFAULT_PORT))),
            control_heartbeat_timeout=_float_env(
                "DICEPP_MANAGER_CONTROL_HEARTBEAT_TIMEOUT", 120.0
            ),
            control_reload_timeout=_float_env(
                "DICEPP_MANAGER_CONTROL_RELOAD_TIMEOUT", 5.0
            ),
            token_path=Path(os.environ.get("DICEPP_MANAGER_TOKEN_FILE", str(layout.manager_token))),
        )


@dataclass(frozen=True, slots=True)
class ManagerClientSettings:
    base_url: str
    token_path: Path
    timeout: float = 10.0

    @classmethod
    def from_layout(cls, layout: InstanceLayout) -> "ManagerClientSettings":
        return cls(
            base_url=os.environ.get("DICEPP_MANAGER_URL", f"http://127.0.0.1:{MANAGER_DEFAULT_PORT}").rstrip("/"),
            token_path=Path(os.environ.get("DICEPP_MANAGER_TOKEN_FILE", str(layout.manager_token))),
            timeout=_float_env("DICEPP_MANAGER_CLIENT_TIMEOUT", 10.0),
        )
