#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os
import sys

# ============================================================
# 打包环境适配：确保 EXE 运行时工作目录和路径正确
# ============================================================
_IS_FROZEN = getattr(sys, 'frozen', False)
_INTERNAL_DIR = None

if _IS_FROZEN:
    # PyInstaller 打包环境
    # 1. 将工作目录切换到 EXE 所在位置，确保 .env 和 Data 目录可被正确访问
    exe_dir = os.path.dirname(sys.executable)
    os.chdir(exe_dir)
    # 2. 记录 _internal 目录路径（pyproject.toml 和 DicePP 插件在这里）
    _INTERNAL_DIR = os.path.join(exe_dir, '_internal')
    if _INTERNAL_DIR not in sys.path:
        sys.path.insert(0, _INTERNAL_DIR)
    # 3. 把 src/plugins 加入 sys.path，让 NoneBot 能正确导入插件
    _plugins_base = os.path.join(_INTERNAL_DIR, 'src', 'plugins')
    if _plugins_base not in sys.path:
        sys.path.insert(0, _plugins_base)
else:
    # 开发环境：保持原有行为
    dir_path = os.path.abspath(os.path.dirname(__file__))
    sys.path.insert(0, dir_path)

import nonebot
from nonebot.log import logger, default_format
from nonebot.adapters.onebot.v11 import Adapter as OneBot_V11_Adapter

# 日志配置：控制台显示 INFO 及以上，错误写入文件
logger.remove()
logger.add(sys.stderr,
           level="INFO",
           format=default_format)
logger.add("error.log",
           rotation="10 MB",
           diagnose=False,
           level="ERROR",
           format=default_format,
           delay=True)  # 延迟创建：只有真正写入错误时才创建文件

# 初始化 NoneBot
nonebot.init()

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
    logger.debug("测试模式: 可运行 scripts\\test\\test_bot.bat 进行验证")
    logger.debug("测试时的 ApiNotAvailable 警告属于正常现象 (无真实客户端接收响应)")

# 注册适配器
driver = nonebot.get_driver()
driver.register_adapter(OneBot_V11_Adapter)

# 加载插件
if _IS_FROZEN:
    # 打包环境：src/plugins 已加入 sys.path，直接用模块名加载
    nonebot.load_plugin("DicePP")
else:
    # 开发环境：使用 load_plugins 扫描目录
    _plugins_dir = os.path.join("src", "plugins")
    nonebot.load_plugins(_plugins_dir)

app = nonebot.get_asgi()

if __name__ == "__main__":
    nonebot.run(app="__mp_main__:app")
