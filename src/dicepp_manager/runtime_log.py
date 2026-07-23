"""Manager-owned runtime log helpers, independent from Dashboard imports."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from dicepp_data import InstanceLayout


def runtime_log_path() -> Path:
    override = os.environ.get("DICEPP_RUNTIME_LOG")
    if override:
        return Path(override)
    root = Path(__file__).resolve().parents[2]
    return InstanceLayout.from_env(root).runtime_log


def append_runtime_log_line(message: str, *, path: str | os.PathLike[str] | None = None) -> None:
    log_path = Path(path) if path else runtime_log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(f"{datetime.now().isoformat(timespec='seconds')} {message.rstrip()}\n")
