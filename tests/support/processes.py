"""Helpers for subprocesses owned by integration tests."""

from __future__ import annotations

import subprocess
import warnings
from collections.abc import Callable
from typing import Any


def stop_server_process(
    process: subprocess.Popen[Any],
    *,
    name: str,
    request_stop: Callable[[], None],
    timeout: float = 10,
) -> None:
    """Ask a test-owned server to exit, then force it only after a timeout."""
    if process.poll() is not None:
        process.wait()
        return

    try:
        request_stop()
    except Exception as exc:
        _warn_force_stop(name, f"优雅退出请求失败：{exc}")
        _force_stop(process, timeout=timeout)
        return

    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        _warn_force_stop(name, f"等待 {timeout:g} 秒后仍未退出")
        _force_stop(process, timeout=timeout)


def _force_stop(process: subprocess.Popen[Any], *, timeout: float) -> None:
    if process.poll() is None:
        process.kill()

    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"强制终止进程 {process.pid} 后仍无法回收") from exc


def _warn_force_stop(name: str, reason: str) -> None:
    warnings.warn(
        f"{name} 未能优雅退出（{reason}），将强制终止进程。",
        RuntimeWarning,
        stacklevel=3,
    )
