# -*- mode: python ; coding: utf-8 -*-
"""
DicePP PyInstaller Spec 文件

用于将 DicePP 骰子机器人打包为 Windows EXE。
使用方法: pyinstaller dicepp.spec

打包模式: 目录模式 (--onedir)
"""

import os
import sys
from PyInstaller.utils.hooks import collect_submodules, collect_data_files

# ============================================================
# 基础配置
# ============================================================
block_cipher = None

# 项目根目录 (spec 文件在 scripts/build/ 目录下，需要向上两级)
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(SPEC), '..', '..'))

# ============================================================
# Hidden Imports - PyInstaller 静态分析无法发现的模块
# ============================================================
hiddenimports = [
    # NoneBot2 核心及适配器
    'nonebot',
    'nonebot.log',
    'nonebot.config',
    'nonebot.plugin',
    'nonebot.adapters',
    'nonebot.adapters.onebot',
    'nonebot.adapters.onebot.v11',
    'nonebot.adapters.onebot.v11.adapter',
    'nonebot.adapters.onebot.v11.bot',
    'nonebot.adapters.onebot.v11.event',
    'nonebot.adapters.onebot.v11.message',
    
    # ASGI 服务器 - uvicorn
    'uvicorn',
    'uvicorn.config',
    'uvicorn.main',
    'uvicorn.server',
    'uvicorn.logging',
    'uvicorn.loops',
    'uvicorn.loops.auto',
    'uvicorn.protocols',
    'uvicorn.protocols.http',
    'uvicorn.protocols.http.auto',
    'uvicorn.protocols.websockets',
    'uvicorn.protocols.websockets.auto',
    'uvicorn.lifespan',
    'uvicorn.lifespan.on',
    
    # FastAPI / Starlette
    'fastapi',
    'fastapi.applications',
    'fastapi.routing',
    'starlette',
    'starlette.applications',
    'starlette.routing',
    'starlette.middleware',
    'starlette.websockets',
    
    # HTTP 客户端
    'aiohttp',
    'aiofiles',
    
    # SQLite (async)
    'aiosqlite',
    
    # 数据处理
    'openpyxl',
    'openpyxl.cell',
    'openpyxl.workbook',
    'openpyxl.worksheet',
    'sqlite3',
    'json',
    
    # 项目依赖
    'zhconv',
    'rsa',
    'psutil',
    'requests',
    'charset_normalizer',
    'chardet',
    'lxml',
    'lxml.etree',
    'docx',

    # lark 解析器 (DicePP 骰子表达式引擎依赖，动态导入无法被静态分析发现)
    'lark',
    'lark.parsers',
    'lark.parsers.earley',
    'lark.parsers.earley_common',
    'lark.parsers.earley_forest',
    'lark.parsers.lalr_interactive_parser',
    'lark.parsers.lalr_analysis',
    'lark.parsers.lalr_traditional',
    'lark.parsers.xearley',
    'lark.grammars',
    'lark.tools',
    
    # Python 标准库动态导入
    'asyncio',
    'ssl',
    'certifi',
    'encodings',
    'encodings.idna',
    
    # Pydantic (NoneBot2 依赖)
    'pydantic',
    'pydantic.fields',
    
    # 日志
    'loguru',
    'loguru._logger',
]

# 自动收集 nonebot 所有子模块
hiddenimports += collect_submodules('nonebot')
hiddenimports += collect_submodules('nonebot_adapter_onebot')
hiddenimports += collect_submodules('lark')

# ============================================================
# Data Files - 需要打包的非 Python 文件
# ============================================================
datas = [
    # NoneBot 配置文件（打包到根目录）
    (os.path.join(PROJECT_ROOT, '.env'), '.'),
    (os.path.join(PROJECT_ROOT, 'pyproject.toml'), '.'),
    
    # DicePP 插件目录 - 保持与 pyproject.toml 中 plugin_dirs 一致的结构
    (os.path.join(PROJECT_ROOT, 'src', 'plugins', 'DicePP'), os.path.join('src', 'plugins', 'DicePP')),

    # config/ 目录：打包全局默认配置和 bot 账号模板
    # 运行时数据（data/）和用户内容（content/）由用户自行挂载，不打包
    (os.path.join(PROJECT_ROOT, 'config', 'global.json'),           os.path.join('config')),
    (os.path.join(PROJECT_ROOT, 'config', 'bots', '_template.json'), os.path.join('config', 'bots')),
]

# personas 目录（包含 default.json 及用户自定义）
personas_src = os.path.join(PROJECT_ROOT, 'config', 'personas')
if os.path.isdir(personas_src):
    datas.append((personas_src, os.path.join('config', 'personas')))

# 收集 nonebot 的数据文件
datas += collect_data_files('nonebot')
datas += collect_data_files('nonebot_adapter_onebot')

# zhconv 库需要 zhcdict.json 字典文件
datas += collect_data_files('zhconv')

# lark 语法文件（.lark grammar files，运行时加载）
datas += collect_data_files('lark')

# ============================================================
# Analysis
# ============================================================
a = Analysis(
    [os.path.join(PROJECT_ROOT, 'bot.py')],
    pathex=[
        PROJECT_ROOT,
        os.path.join(PROJECT_ROOT, 'src', 'plugins', 'DicePP'),
    ],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # 排除不需要的大型包
        'matplotlib',
        'numpy',
        'pandas',
        'scipy',
        'PIL',
        'cv2',
        'tensorflow',
        'torch',
        'tkinter',
        '_tkinter',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# ============================================================
# PYZ - Python 字节码归档
# ============================================================
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# ============================================================
# EXE - 可执行文件
# ============================================================
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,  # 目录模式
    name='DicePP',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,  # 使用 UPX 压缩（如果可用）
    console=True,  # 控制台应用（显示日志）
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # 版本信息（可选，后续可添加）
    # version='version_info.txt',
    # icon='icon.ico',
)

# ============================================================
# COLLECT - 收集所有文件到 dist/DicePP/
# ============================================================
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='DicePP',
)