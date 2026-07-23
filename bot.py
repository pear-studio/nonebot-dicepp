#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os
import sys

# ============================================================
# 打包环境适配：确保 EXE 运行时工作目录和路径正确
# ============================================================
_IS_FROZEN = getattr(sys, "frozen", False)
if _IS_FROZEN:
    # PyInstaller 打包环境
    # 将工作目录切换到 EXE 所在位置，确保用户数据目录可被正确访问。
    # Python imports come from PyInstaller's importer/embedded PYZ; do not
    # depend on a copied ``_internal/src`` source tree.
    exe_dir = os.path.dirname(sys.executable)
    os.chdir(exe_dir)
else:
    # 源码环境只暴露 ``src``，禁止 DicePP 内部模块作为顶级包被导入。
    dir_path = os.path.abspath(os.path.dirname(__file__))
    _src_root = os.path.join(dir_path, "src")
    if _src_root not in sys.path:
        sys.path.insert(0, _src_root)

from plugins.DicePP.utils.stdio import configure_redirected_stdio_utf8

configure_redirected_stdio_utf8()

# ============================================================
# Bootstrap CLI — 在 nonebot.init() 之前解析
# ============================================================
import argparse

_bootstrap_parser = argparse.ArgumentParser(add_help=False)
_bootstrap_parser.add_argument('--version', action='store_true')
_bootstrap_parser.add_argument('--smoke-check', action='store_true')
_bootstrap_args, _ = _bootstrap_parser.parse_known_args()

if _bootstrap_args.version:
    from importlib.metadata import version
    print(f"DicePP v{version('dicepp')}")
    sys.exit(0)

if _bootstrap_args.smoke_check:
    from plugins.DicePP import _smoke_check
    ok = _smoke_check.run_smoke_check()
    sys.exit(0 if ok else 1)

import nonebot
from nonebot.adapters.onebot.v11 import Adapter as OneBot_V11_Adapter
from plugins.DicePP.utils.logger import logger, restore_runtime_logging

restore_runtime_logging()

# 初始化 NoneBot。DicePP 不依赖根目录 .env；这些是当前 OneBot 运行形态的默认值。
nonebot.init(
    _env_file=("config/nonebot.env",),
    host="0.0.0.0",
    port=8080,
    command_start={""},
    command_sep={""},
)
restore_runtime_logging()

# 显示启动信息
@nonebot.get_driver().on_startup
async def _startup_message():
    """Bot 启动后显示提示信息"""
    logger.info("=" * 50)
    logger.info("DicePP 骰子机器人已启动!")
    logger.info("=" * 50)
    logger.info("等待聊天客户端连接...")
    logger.info("请确保您的聊天客户端 (如 LLBot) 已正确配置并连接")
    logger.info("=" * 50)
    
    # 技术细节和测试信息只在 DEBUG 级别显示
    logger.debug("正在监听 OneBot V11 协议连接...")
    logger.debug("测试模式: 可使用 uv run pytest 进行验证")
    logger.debug("测试时的 ApiNotAvailable 警告属于正常现象 (无真实客户端接收响应)")

# 注册适配器
driver = nonebot.get_driver()
driver.register_adapter(OneBot_V11_Adapter)

# 加载规范的 NoneBot side-effect 入口。
nonebot.load_plugin("plugins.DicePP.plugin")

app = nonebot.get_asgi()

if __name__ == "__main__":
    nonebot.run(app="__mp_main__:app")
