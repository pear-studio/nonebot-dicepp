#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import argparse
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

# ============================================================
# Bootstrap CLI — 在 nonebot.init() 之前解析
# ============================================================
_bootstrap_parser = argparse.ArgumentParser(add_help=False)
_bootstrap_parser.add_argument('--version', action='store_true')
_bootstrap_args, _ = _bootstrap_parser.parse_known_args()

if _bootstrap_args.version:
    from importlib.metadata import version
    print(f"DicePP v{version('dicepp')}")
    sys.exit(0)

from plugins.DicePP.utils.stdio import configure_redirected_stdio_utf8

configure_redirected_stdio_utf8()

import nonebot
from nonebot.adapters.onebot.v11 import Adapter as OneBot_V11_Adapter
from plugins.DicePP.utils.logger import logger, restore_runtime_logging

restore_runtime_logging()

# OneBot 监听地址：默认只绑回环，避免 Windows 首启触发防火墙入站规则。
# Docker 部署由 docker-compose.yml 显式设置 DICEPP_ONEBOT_HOST=0.0.0.0（跨容器连接要求）。
_onebot_host = os.environ.get("DICEPP_ONEBOT_HOST", "").strip() or "127.0.0.1"

# 初始化 NoneBot。DicePP 不依赖根目录 .env；这些是当前 OneBot 运行形态的默认值。
nonebot.init(
    _env_file=("config/nonebot.env",),
    host=_onebot_host,
    port=8080,
    command_start={""},
    command_sep={""},
)
restore_runtime_logging()
logger.info(f"OneBot 监听地址: {_onebot_host}:8080")

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
    
    # 技术细节只在 DEBUG 级别显示
    logger.debug("正在监听 OneBot V11 协议连接...")

# 注册适配器
driver = nonebot.get_driver()
driver.register_adapter(OneBot_V11_Adapter)

# Load DicePP through NoneBot's managed plugin path, then prove that its
# matchers and business command registry are actually registered.  NoneBot
# otherwise returns None for import failures and would let the server start
# without DicePP's command handling.
from plugins.DicePP.runtime_preflight import (
    load_and_validate_dicepp_plugin,
)

load_and_validate_dicepp_plugin()

app = nonebot.get_asgi()

if __name__ == "__main__":
    nonebot.run(app="__mp_main__:app")
