"""
frozen.py - 打包环境检测与路径适配层

提供统一的路径解析接口，同时支持：
- 开发环境：直接运行 Python 脚本
- 打包环境：PyInstaller 生成的 EXE
"""

import sys
import os

APP_DIR_ENV_KEY = "DICEPP_APP_DIR"
PROJECT_ROOT_ENV_KEY = "DICEPP_PROJECT_ROOT"


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
    env_app_dir = os.getenv(APP_DIR_ENV_KEY)
    if env_app_dir:
        return os.path.abspath(env_app_dir)

    if is_frozen():
        # sys.executable 指向 DicePP.exe 的完整路径
        return os.path.dirname(sys.executable)
    else:
        # 开发环境: frozen.py 位于 utils/ 下，向上两级到 DicePP/
        return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def get_project_root() -> str:
    """
    获取项目根目录（config/、data/、content/ 所在的目录）。

    优先级:
    - 环境变量 DICEPP_PROJECT_ROOT 优先（Docker/打包环境兜底）
    - 打包环境: EXE 所在目录
    - 开发环境: 从 src/plugins/DicePP/ 向上 3 级到达项目根

    Returns:
        项目根目录的绝对路径
    """
    env_root = os.getenv(PROJECT_ROOT_ENV_KEY)
    if env_root:
        return os.path.abspath(env_root)

    if is_frozen():
        return os.path.dirname(sys.executable)

    # 开发/Docker: frozen.py 位于 utils/ 下
    # utils/ -> DicePP/ -> plugins/ -> src/ -> 项目根
    utils_dir = os.path.dirname(os.path.abspath(__file__))
    dicepp_dir = os.path.dirname(utils_dir)
    plugins_dir = os.path.dirname(dicepp_dir)
    src_dir = os.path.dirname(plugins_dir)
    project_root = os.path.dirname(src_dir)
    return project_root


def get_runtime_info() -> dict:
    """
    获取运行时环境信息，用于调试和日志。
    
    Returns:
        包含环境信息的字典
    """
    return {
        'frozen': is_frozen(),
        'app_dir': get_app_dir(),
        'project_root': get_project_root(),
        'executable': sys.executable,
        'cwd': os.getcwd(),
    }
