"""Shared runtime log helpers for the Windows launcher and process backend."""

from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Callable

from .config import DashboardPaths


def runtime_log_path() -> Path:
    """Return the current shared runtime log path."""
    return DashboardPaths.runtime_log_path()


def rotate_runtime_log(
    path: str | os.PathLike[str] | None = None,
    *,
    keep: int = 10,
    now: Callable[[], datetime] | None = None,
) -> Path:
    """Rotate the current runtime log once for a new launcher lifecycle."""
    log_path = Path(path) if path is not None else runtime_log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    if log_path.exists() and log_path.is_file() and log_path.stat().st_size > 0:
        timestamp = (now or datetime.now)().strftime("%Y%m%d-%H%M%S")
        rotated = log_path.with_name(f"dicepp-runtime-{timestamp}.log")
        suffix = 1
        while rotated.exists():
            rotated = log_path.with_name(
                f"dicepp-runtime-{timestamp}-{suffix}.log"
            )
            suffix += 1
        log_path.replace(rotated)
    log_path.touch(exist_ok=True)
    prune_runtime_logs(log_path.parent, keep=keep)
    return log_path


def prune_runtime_logs(directory: str | os.PathLike[str], *, keep: int = 10) -> None:
    """Keep only the newest rotated runtime logs."""
    if keep < 0:
        raise ValueError("keep must be non-negative")
    log_dir = Path(directory)
    histories = sorted(
        (
            path
            for path in log_dir.glob("dicepp-runtime-*.log")
            if path.is_file()
        ),
        key=lambda path: path.name,
        reverse=True,
    )
    for stale in histories[keep:]:
        try:
            stale.unlink()
        except FileNotFoundError:
            pass


def append_runtime_log_line(
    message: str,
    *,
    path: str | os.PathLike[str] | None = None,
) -> None:
    """Append one timestamped launcher/runtime diagnostic line."""
    log_path = Path(path) if path is not None else runtime_log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().isoformat(timespec="seconds")
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(f"{timestamp} {message.rstrip()}\n")


def configure_file_logging(path: str | os.PathLike[str] | None = None) -> Path:
    """Send Python logging from launcher/Dashboard code to the runtime log."""
    log_path = Path(path) if path is not None else runtime_log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=str(log_path),
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
        force=True,
    )
    return log_path
