"""
测试 frozen.py 模块 - 路径适配层

覆盖场景：
- 开发环境检测
- Mock 打包环境检测
- 路径解析正确性
"""

import sys
import os
from pathlib import Path

import pytest
from unittest.mock import patch

# 被测模块
from plugins.DicePP.frozen import (
    PROJECT_ROOT_ENV_KEY,
    get_app_dir,
    get_project_root,
    get_runtime_info,
    is_frozen,
)


class TestIsFrozen:
    """测试 is_frozen() 函数"""

    def test_development_environment(self):
        """开发环境下应返回 False"""
        # 开发环境默认没有 sys.frozen 属性
        assert is_frozen() is False

    def test_frozen_environment(self):
        """模拟打包环境应返回 True"""
        with patch.object(sys, "frozen", True, create=True):
            assert is_frozen() is True


class TestGetAppDir:
    """测试 get_app_dir() 函数"""

    def test_development_environment_path(self, monkeypatch):
        """开发环境下应返回 DicePP 目录"""
        monkeypatch.delenv("DICEPP_APP_DIR", raising=False)
        app_dir = get_app_dir()
        # 应该是绝对路径
        assert os.path.isabs(app_dir)
        # 应该以 DicePP 结尾（或包含 DicePP）
        assert 'DicePP' in app_dir
        # 目录应该存在
        assert os.path.isdir(app_dir)

    def test_frozen_environment_path(self, monkeypatch):
        """模拟打包环境应返回 EXE 所在目录"""
        monkeypatch.delenv("DICEPP_APP_DIR", raising=False)
        expected_dir = os.path.join(os.sep, 'Program Files', 'DicePP')
        fake_exe_path = os.path.join(expected_dir, 'DicePP.exe')
        
        with patch.object(sys, "frozen", True, create=True):
            with patch.object(sys, 'executable', fake_exe_path):
                app_dir = get_app_dir()
                assert app_dir == expected_dir

    def test_frozen_environment_with_unicode_path(self, monkeypatch):
        """模拟中文路径的打包环境"""
        monkeypatch.delenv("DICEPP_APP_DIR", raising=False)
        expected_dir = os.path.join(os.sep, '测试目录', '骰子机器人')
        fake_exe_path = os.path.join(expected_dir, 'DicePP.exe')
        
        with patch.object(sys, "frozen", True, create=True):
            with patch.object(sys, 'executable', fake_exe_path):
                app_dir = get_app_dir()
                assert app_dir == expected_dir

class TestGetRuntimeInfo:
    """测试 get_runtime_info() 函数"""

    def test_returns_dict_with_required_keys(self):
        """应返回包含所有必需键的字典"""
        info = get_runtime_info()

        assert isinstance(info, dict)
        assert 'frozen' in info
        assert 'app_dir' in info
        assert 'project_root' in info
        assert 'executable' in info
        assert 'cwd' in info

    def test_development_environment_info(self):
        """开发环境的运行时信息"""
        info = get_runtime_info()

        assert info['frozen'] is False
        assert os.path.isabs(info['app_dir'])
        assert info['executable'] == sys.executable
        assert info['cwd'] == os.getcwd()

    def test_frozen_environment_info(self):
        """模拟打包环境的运行时信息"""
        fake_exe_path = os.path.join(os.sep, 'Apps', 'DicePP', 'DicePP.exe')

        with patch.object(sys, "frozen", True, create=True):
            with patch.object(sys, 'executable', fake_exe_path):
                info = get_runtime_info()

                assert info['frozen'] is True
                assert info['executable'] == fake_exe_path


class TestGetProjectRoot:

    def test_dev_environment_returns_repo_root(self, monkeypatch):
        monkeypatch.delenv(PROJECT_ROOT_ENV_KEY, raising=False)
        repo_root = next(
            parent
            for parent in Path(__file__).resolve().parents
            if (parent / "pyproject.toml").is_file()
        )
        assert Path(get_project_root()) == repo_root

    def test_env_var_override(self, tmp_path, monkeypatch):
        monkeypatch.setenv(PROJECT_ROOT_ENV_KEY, str(tmp_path))
        assert get_project_root() == os.path.abspath(str(tmp_path))

    def test_returns_absolute_path(self):
        result = get_project_root()
        assert os.path.isabs(result)

    def test_frozen_environment(self, monkeypatch):
        expected_dir = os.path.join(os.sep, 'Apps', 'DicePP')
        fake_exe = os.path.join(expected_dir, 'DicePP.exe')
        monkeypatch.delenv(PROJECT_ROOT_ENV_KEY, raising=False)
        with patch.object(sys, "frozen", True, create=True):
            with patch.object(sys, 'executable', fake_exe):
                result = get_project_root()
        assert result == expected_dir
