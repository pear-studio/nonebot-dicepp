"""
测试 frozen.py 模块 - 路径适配层

覆盖场景：
- 开发环境检测
- Mock 打包环境检测
- 路径解析正确性
"""

import sys
import os
import pytest
from unittest.mock import patch

# 被测模块
from utils.frozen import is_frozen, get_app_dir, get_data_dir, get_runtime_info


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

    def test_development_environment_path(self):
        """开发环境下应返回 DicePP 目录"""
        app_dir = get_app_dir()
        # 应该是绝对路径
        assert os.path.isabs(app_dir)
        # 应该以 DicePP 结尾（或包含 DicePP）
        assert 'DicePP' in app_dir
        # 目录应该存在
        assert os.path.isdir(app_dir)

    def test_frozen_environment_path(self):
        """模拟打包环境应返回 EXE 所在目录"""
        fake_exe_path = r'C:\Program Files\DicePP\DicePP.exe'
        expected_dir = r'C:\Program Files\DicePP'
        
        with patch.object(sys, 'frozen', True, create=True):
            with patch.object(sys, 'executable', fake_exe_path):
                app_dir = get_app_dir()
                assert app_dir == expected_dir

    def test_frozen_environment_with_unicode_path(self):
        """模拟中文路径的打包环境"""
        fake_exe_path = r'D:\测试目录\骰子机器人\DicePP.exe'
        expected_dir = r'D:\测试目录\骰子机器人'
        
        with patch.object(sys, 'frozen', True, create=True):
            with patch.object(sys, 'executable', fake_exe_path):
                app_dir = get_app_dir()
                assert app_dir == expected_dir


class TestGetDataDir:
    """测试 get_data_dir() 函数"""

    def test_data_dir_under_app_dir(self):
        """Data 目录应位于 app_dir 下"""
        data_dir = get_data_dir()
        app_dir = get_app_dir()
        
        assert data_dir == os.path.join(app_dir, 'Data')

    def test_frozen_environment_data_path(self):
        """模拟打包环境的 Data 目录路径"""
        fake_exe_path = r'C:\Apps\DicePP\DicePP.exe'
        expected_data_dir = r'C:\Apps\DicePP\Data'
        
        with patch.object(sys, 'frozen', True, create=True):
            with patch.object(sys, 'executable', fake_exe_path):
                data_dir = get_data_dir()
                assert data_dir == expected_data_dir


class TestGetRuntimeInfo:
    """测试 get_runtime_info() 函数"""

    def test_returns_dict_with_required_keys(self):
        """应返回包含所有必需键的字典"""
        info = get_runtime_info()
        
        assert isinstance(info, dict)
        assert 'frozen' in info
        assert 'app_dir' in info
        assert 'data_dir' in info
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
        fake_exe_path = r'C:\Apps\DicePP\DicePP.exe'
        
        with patch.object(sys, 'frozen', True, create=True):
            with patch.object(sys, 'executable', fake_exe_path):
                info = get_runtime_info()
                
                assert info['frozen'] is True
                assert info['executable'] == fake_exe_path
