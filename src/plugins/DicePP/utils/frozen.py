"""
frozen.py - 打包环境检测与路径适配层

提供统一的路径解析接口，同时支持：
- 开发环境：直接运行 Python 脚本
- 打包环境：PyInstaller 生成的 EXE
"""

import sys
import os


def is_frozen() -> bool:
    """
    检测是否在 PyInstaller 打包环境中运行。
    
    Returns:
        True 表示运行在打包后的 EXE 中
        False 表示运行在开发环境（直接 Python 运行）
    """
    return getattr(sys, 'frozen', False)


def get_app_dir() -> str:
    """
    获取应用根目录。
    
    - 打包环境: EXE 所在目录（如 dist/DicePP/）
    - 开发环境: src/plugins/DicePP 目录
    
    Returns:
        应用根目录的绝对路径
    """
    if is_frozen():
        # sys.executable 指向 DicePP.exe 的完整路径
        return os.path.dirname(sys.executable)
    else:
        # 开发环境: frozen.py 位于 utils/ 下，向上两级到 DicePP/
        return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def get_data_dir() -> str:
    """
    获取 Data 目录路径。
    
    Data 目录始终位于应用根目录下，用于存储用户数据。
    
    Returns:
        Data 目录的绝对路径
    """
    return os.path.join(get_app_dir(), 'Data')


def get_runtime_info() -> dict:
    """
    获取运行时环境信息，用于调试和日志。
    
    Returns:
        包含环境信息的字典
    """
    return {
        'frozen': is_frozen(),
        'app_dir': get_app_dir(),
        'data_dir': get_data_dir(),
        'executable': sys.executable,
        'cwd': os.getcwd(),
    }
