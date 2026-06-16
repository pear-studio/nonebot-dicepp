"""
Tests for core/config/declare.py

覆盖:
  - get_bot_version() 返回 vX.Y.Z 格式
  - get_bot_version() 与 pyproject.toml 中的版本一致
"""
import sys
import tomllib
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent.parent / "src" / "plugins" / "DicePP"
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

project_root = Path(__file__).resolve().parent.parent.parent.parent


def _read_pyproject_version() -> str:
    """读取 pyproject.toml 中声明的项目版本号，返回不带 v 前缀的值。"""
    pyproject = project_root / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    return data["project"]["version"]


class TestGetBotVersion:
    """验证 get_bot_version() 与包元数据 / pyproject.toml 一致。"""

    def test_returns_v_prefix_semver(self):
        """get_bot_version() 返回 'vX.Y.Z' 格式。"""
        from core.config.declare import get_bot_version

        version_str = get_bot_version()
        # 格式: v + 三段数字
        parts = version_str[1:].split(".")
        assert version_str.startswith("v"), f"版本号必须以 v 开头: {version_str}"
        assert len(parts) == 3, f"版本号必须为三段式 semver: {version_str}"
        assert all(p.isdigit() for p in parts), f"每段必须为数字: {version_str}"

    def test_matches_pyproject_version(self):
        """get_bot_version() 与 pyproject.toml 中的版本一致。"""
        from core.config.declare import get_bot_version

        expected = f"v{_read_pyproject_version()}"
        assert get_bot_version() == expected, (
            f"get_bot_version() = {get_bot_version()} "
            f"与 pyproject.toml 的 version = {expected} 不一致"
        )

    def test_importlib_matches_pyproject(self):
        """importlib.metadata.version('dicepp') 与 pyproject.toml 一致。"""
        from importlib.metadata import version as _get_pkg_version

        assert _get_pkg_version("dicepp") == _read_pyproject_version(), (
            "已安装包的版本与 pyproject.toml 不一致，检查是否需要 uv sync"
        )
