"""
测试用文件系统工具（Windows 下 SQLite 等句柄释放较慢时，递归删除目录需短重试）。
"""
from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

import pytest

DEFAULT_MAX_ATTEMPTS = 20
DEFAULT_SLEEP_S = 0.05


def symlink_or_skip(
    link: Path,
    target: Path,
    *,
    target_is_directory: bool = False,
) -> None:
    """Create a test symlink or skip when Windows denies link privileges."""
    try:
        link.symlink_to(target, target_is_directory=target_is_directory)
    except OSError as exc:
        if os.name == "nt" and getattr(exc, "winerror", None) == 1314:
            pytest.skip("Windows account cannot create symbolic links")
        raise


def rmtree_retry(
    path: str | os.PathLike[str],
    *,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    sleep_s: float = DEFAULT_SLEEP_S,
) -> None:
    """删除目录树；遇 PermissionError 时短暂重试，最终失败直接抛错。"""
    p = os.fspath(path)
    if not p or not os.path.exists(p):
        return

    last_error: PermissionError | None = None
    for _ in range(max_attempts):
        try:
            shutil.rmtree(p)
            return
        except PermissionError as exc:
            last_error = exc
            time.sleep(sleep_s)

    remaining = []
    root = Path(p)
    if root.exists():
        remaining = [str(child.relative_to(root)) for child in root.rglob("*")][:20]
    details = "\n".join(f"  - {item}" for item in remaining) or "  <none listed>"
    raise PermissionError(
        f"Failed to remove test directory after {max_attempts} attempts: {p}\n"
        "Possible leaked SQLite connection, file handle, or background task.\n"
        f"Remaining entries:\n{details}"
    ) from last_error
