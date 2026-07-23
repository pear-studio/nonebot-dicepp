"""
冒烟自检模块 — 在 nonebot.init() 之前运行，验证打包环境完整性。

约定：本模块顶层只 import 标准库。所有 DicePP 模块导入均在函数体内（延迟导入），
确保自身不会因依赖缺失而崩溃。

检查项：
  1. frozen 环境检测 — is_frozen / get_app_dir / get_runtime_info 是否正常
  2. 关键模块导入   — PyInstaller hiddenimports 覆盖的核心依赖能否 import
  3. DicePP 插件导入 — NoneBot 实际加载插件前的 import 链是否完整
  4. 版本号          — importlib.metadata 能否读到正确的 vX.Y.Z 格式版本号
"""

import sys
import os
import importlib


def run_smoke_check() -> bool:
    """执行全部冒烟检查。返回 True 表示全部通过，False 表示有失败项。"""
    errors: list[str] = []

    errors.extend(_check_frozen_env())
    errors.extend(_check_critical_modules())
    errors.extend(_check_dicepp_plugin_import())
    errors.extend(_check_version())

    if errors:
        print(f"\nSMOKE CHECK FAILED: {len(errors)} error(s)")
        for e in errors:
            print(f"  FAIL: {e}")
        return False
    else:
        print("\nSMOKE CHECK PASSED")
        return True


def _check_frozen_env() -> list[str]:
    """验证 frozen 环境检测逻辑。"""
    errors = []
    from plugins.DicePP import frozen

    if frozen.is_frozen() != getattr(sys, "frozen", False):
        errors.append("is_frozen() mismatch with sys.frozen")

    app_dir = frozen.get_app_dir()
    if not app_dir or not os.path.isabs(app_dir):
        errors.append(f"get_app_dir() returned invalid: {app_dir}")

    info = frozen.get_runtime_info()
    for key in ('frozen', 'app_dir', 'project_root', 'executable', 'cwd'):
        if key not in info:
            errors.append(f"get_runtime_info() missing key: {key}")

    return errors


def _check_critical_modules() -> list[str]:
    """验证关键模块可导入。

    此列表是 spec hiddenimports 的超集——包含 PyInstaller 自动收集的模块和手动补的模块。
    加新依赖时请同步更新此列表。
    """
    errors = []
    CRITICAL_MODULES = [
        'lark',
        'aiosqlite',
        'rsa',
        'zhconv',
        'openpyxl',
        'requests',
        'charset_normalizer',
        'chardet',
        'docx',
        'lxml',
        'loguru',
        'aiohttp',
        'aiofiles',
        'uvicorn',
        'fastapi',
        'pydantic',
        'cryptography.fernet',
        'cryptography.hazmat.primitives.kdf.pbkdf2',
    ]
    for mod in CRITICAL_MODULES:
        try:
            importlib.import_module(mod)
        except ImportError as e:
            errors.append(f"Module '{mod}' import failed: {e}")
    return errors


def _check_dicepp_plugin_import() -> list[str]:
    """验证规范的 DicePP 插件入口可导入。"""
    errors = []
    try:
        importlib.import_module("plugins.DicePP.plugin")
    except Exception as e:
        errors.append(
            f"plugins.DicePP.plugin import failed: {e.__class__.__name__}: {e}"
        )

    return errors


def _check_version() -> list[str]:
    """验证版本号可读取且格式正确。

    直接使用 importlib.metadata，不经过 plugins.DicePP.core.config.declare。
    plugins.DicePP.core.config 会触发 basic.py → utils.logger 的完整导入链，
    不适合在 nonebot.init() 之前调用。
    """
    errors = []
    import re
    from importlib.metadata import version

    ver = version('dicepp')
    if not re.match(r'^\d+\.\d+\.\d+', ver):
        errors.append(f"Version format invalid: {ver}")
    return errors
