"""
测试用文件系统工具（Windows 下 SQLite 等句柄释放较慢时，递归删除目录需短重试）。
"""
from __future__ import annotations

import os
import shutil
import time

DEFAULT_MAX_ATTEMPTS = 20
DEFAULT_SLEEP_S = 0.05


def rmtree_retry(
    path: str | os.PathLike[str],
    *,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    sleep_s: float = DEFAULT_SLEEP_S,
) -> None:
    """删除目录树；遇 PermissionError 时短暂重试，最后 ignore_errors 兜底。"""
    p = os.fspath(path)
    if not p or not os.path.exists(p):
        return
    for _ in range(max_attempts):
        try:
            shutil.rmtree(p)
            return
        except PermissionError:
            time.sleep(sleep_s)
    shutil.rmtree(p, ignore_errors=True)
