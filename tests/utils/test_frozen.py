"""
测试 frozen.py 模块 - 路径适配层

覆盖场景：
- 开发环境检测
- Mock 打包环境检测
- 路径解析正确性
"""

import sys
import os
import subprocess
from pathlib import Path

import pytest
from unittest.mock import patch

pytestmark = pytest.mark.unit

# 被测模块
from utils.frozen import is_frozen, get_app_dir, get_runtime_info, get_project_root, PROJECT_ROOT_ENV_KEY


class TestIsFrozen:
    """测试 is_frozen() 函数"""

    def test_development_environment(self):
        """开发环境下应返回 False"""
        # 开发环境默认没有 sys.frozen 属性
        assert is_frozen() is False

    def test_frozen_environment(self):
        """模拟打包环境应返回 True"""
        with patch.object(sys, 'frozen', True, create=True):
            assert is_frozen() is True

    def test_frozen_false_explicitly(self):
        """sys.frozen 显式为 False 时应返回 False"""
        with patch.object(sys, 'frozen', False, create=True):
            assert is_frozen() is False


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
        
        with patch.object(sys, 'frozen', True, create=True):
            with patch.object(sys, 'executable', fake_exe_path):
                app_dir = get_app_dir()
                assert app_dir == expected_dir

    def test_frozen_environment_with_unicode_path(self, monkeypatch):
        """模拟中文路径的打包环境"""
        monkeypatch.delenv("DICEPP_APP_DIR", raising=False)
        expected_dir = os.path.join(os.sep, '测试目录', '骰子机器人')
        fake_exe_path = os.path.join(expected_dir, 'DicePP.exe')
        
        with patch.object(sys, 'frozen', True, create=True):
            with patch.object(sys, 'executable', fake_exe_path):
                app_dir = get_app_dir()
                assert app_dir == expected_dir

    def test_dicepp_app_dir_env_override(self, monkeypatch, tmp_path):
        """设置 DICEPP_APP_DIR 时 get_app_dir 应返回其绝对路径（不依赖 dev/frozen 分支）"""
        override = tmp_path / "custom_app_root"
        override.mkdir()
        monkeypatch.setenv("DICEPP_APP_DIR", str(override))
        assert get_app_dir() == os.path.abspath(str(override))


def test_dicepp_app_dir_sets_config_data_path(tmp_path):
    """
    子进程中首次导入 core.config.basic 时，Paths.PROJECT_ROOT 应落在项目根目录下，
    Paths.CONFIG_DIR 应为 PROJECT_ROOT/config。
    （主 pytest 进程已导入过 config，需独立进程验证 import-time 行为。）
    """
    app_root = tmp_path / "dicepp_app_root"
    app_root.mkdir()
    dicepp_src = Path(__file__).resolve().parents[2] / "src" / "plugins" / "DicePP"
    script = f"""
import os, sys
sys.path.insert(0, {str(dicepp_src)!r})
import core.config.basic as basic_mod
expected_root = os.path.abspath({str(app_root)!r})
assert str(basic_mod.Paths.PROJECT_ROOT) == expected_root, (str(basic_mod.Paths.PROJECT_ROOT), expected_root)
assert str(basic_mod.Paths.CONFIG_DIR) == os.path.join(expected_root, "config"), str(basic_mod.Paths.CONFIG_DIR)
"""
    env = os.environ.copy()
    env["DICEPP_PROJECT_ROOT"] = str(app_root)
    proc = subprocess.run(
        [sys.executable, "-c", script],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


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


class TestGetProjectRoot:

    def test_dev_environment_returns_repo_root(self):
        repo_root = Path(__file__).resolve().parents[2]
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
        with patch.object(sys, 'frozen', True, create=True):
            with patch.object(sys, 'executable', fake_exe):
                result = get_project_root()
        assert result == expected_dir

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
        
        with patch.object(sys, 'frozen', True, create=True):
            with patch.object(sys, 'executable', fake_exe_path):
                info = get_runtime_info()
                
                assert info['frozen'] is True
                assert info['executable'] == fake_exe_path
