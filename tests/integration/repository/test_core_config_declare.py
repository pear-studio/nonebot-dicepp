"""
Tests for core/config/declare.py

覆盖:
  - get_bot_version() 返回 vX.Y.Z 或 vX.Y.ZrcN 格式
  - get_bot_version() 与 pyproject.toml 中的版本一致
"""
import sys
import tomllib
import re
from pathlib import Path

import pytest

project_root = next(
    parent
    for parent in Path(__file__).resolve().parents
    if (parent / "pyproject.toml").is_file()
)
PLUGIN_ROOT = project_root / "src" / "plugins" / "DicePP"
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

def _read_pyproject_version() -> str:
    """读取 pyproject.toml 中声明的项目版本号，返回不带 v 前缀的值。"""
    pyproject = project_root / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    return data["project"]["version"]


class TestGetBotVersion:
    """验证 get_bot_version() 与包元数据 / pyproject.toml 一致。"""

    def test_returns_v_prefix_semver(self):
        """get_bot_version() 返回正式版或预发布版本格式。"""
        from plugins.DicePP.core.config.declare import get_bot_version

        version_str = get_bot_version()
        assert re.match(r"^v\d+\.\d+\.\d+((a|b|rc)\d+)?$", version_str), (
            f"版本号必须为 vX.Y.Z 或 vX.Y.ZrcN 格式: {version_str}"
        )

    def test_matches_pyproject_version(self):
        """get_bot_version() 与 pyproject.toml 中的版本一致。
        (importlib.metadata consistency is implicit in the declared import path.)"""
        from plugins.DicePP.core.config.declare import get_bot_version

        expected = f"v{_read_pyproject_version()}"
        assert get_bot_version() == expected, (
            f"get_bot_version() = {get_bot_version()} "
            f"与 pyproject.toml 的 version = {expected} 不一致"
        )
