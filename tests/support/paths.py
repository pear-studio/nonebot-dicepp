"""Stable repository path helpers for tests and test-support scripts."""

from __future__ import annotations

from pathlib import Path


def find_repository_root(start: Path) -> Path:
    """Find the checkout root by repository sentinels instead of parent depth."""
    candidate = start.resolve()
    if candidate.is_file():
        candidate = candidate.parent
    for directory in (candidate, *candidate.parents):
        if (directory / "pyproject.toml").is_file() and (directory / "src").is_dir():
            return directory
    raise RuntimeError(f"无法从 {start} 定位 DicePP 仓库根目录")
