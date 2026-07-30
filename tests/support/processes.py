"""Helpers for subprocesses owned by integration tests."""

from __future__ import annotations

import os
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
    kill_tree: bool = False,
    force_kill_tree: bool = False,
) -> None:
    """Ask a test-owned server to exit, then force it only after a timeout.

    ``force_kill_tree`` is for a known test-owned Windows process root whose
    descendants inherit its standard handles. It terminates the root and
    descendants before the root can exit and orphan them.
    """
    if process.poll() is not None:
        process.wait()
        return

    if force_kill_tree and os.name == "nt":
        _force_stop(process, timeout=timeout, kill_tree=True)
        return

    try:
        request_stop()
    except Exception as exc:
        _warn_force_stop(name, f"优雅退出请求失败：{exc}")
        _force_stop(process, timeout=timeout, kill_tree=kill_tree)
        return

    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        _warn_force_stop(name, f"等待 {timeout:g} 秒后仍未退出")
        _force_stop(process, timeout=timeout, kill_tree=kill_tree)


def _force_stop(
    process: subprocess.Popen[Any],
    *,
    timeout: float,
    kill_tree: bool,
) -> None:
    if process.poll() is None:
        if kill_tree and os.name == "nt":
            _kill_windows_process_tree(process)
        else:
            process.kill()

    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"强制终止进程 {process.pid} 后仍无法回收") from exc


def _kill_windows_process_tree(process: subprocess.Popen[Any]) -> None:
    """Terminate a verified test-owned Windows process and all descendants."""
    try:
        result = subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode != 0 and process.poll() is None:
            process.kill()
    except subprocess.TimeoutExpired:
        process.kill()


def _warn_force_stop(name: str, reason: str) -> None:
    warnings.warn(
        f"{name} 未能优雅退出（{reason}），将强制终止进程。",
        RuntimeWarning,
        stacklevel=3,
    )


def format_server_startup_failure(
    process: subprocess.Popen[Any],
    *,
    name: str,
    url: str,
    elapsed_seconds: float,
    output: str,
) -> str:
    """Render actionable diagnostics after a test-owned server misses startup."""
    captured_output = output.strip() or "<no stdout/stderr captured>"
    return (
        f"{name} did not become ready after {elapsed_seconds:.2f}s "
        f"at {url} (pid={process.pid}, returncode={process.poll()!r}).\n"
        f"stdout/stderr:\n{captured_output}"
    )
