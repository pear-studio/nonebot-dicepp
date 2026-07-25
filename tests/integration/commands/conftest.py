"""Fixture registration for full-Bot command integration tests."""

from __future__ import annotations

import uuid

import pytest

from plugins.DicePP.core.bot import Bot
from tests.support.bot import TestProxy
from tests.support.core_command import IntegrationHelper
from tests.support.fs_utils import rmtree_retry


@pytest.fixture(scope="module")
async def bot():
    """每个测试模块一个独立 Bot（独立 DB、配置与数据目录）。

    指令集成测试允许同一文件内的测试按顺序共享 Bot 状态（例如同 class
    内先建卡后删卡），但跨文件必须隔离：共享 Bot 会让先攻、昵称、缓存等
    可变状态经默认群/用户键空间泄漏到后续文件。module 边界整体重建 Bot
    覆盖 Bot 实例持有的全部可变状态（全部 DB 表、配置、缓存），无需
    逐表枚举清理。
    """
    bot_instance = Bot(
        f"test_module_{uuid.uuid4().hex[:12]}",
        readonly=True,
        no_tick=True,
    )
    bot_instance.config.master = ["test_master"]
    proxy = TestProxy()
    bot_instance.set_client_proxy(proxy)
    await bot_instance.delay_init_command()
    proxy.mute = True
    bot_instance.loc_helper.load_localization()
    bot_instance.loc_helper.load_chat()
    yield bot_instance
    try:
        await bot_instance.shutdown_async()
    finally:
        rmtree_retry(bot_instance.data_path)


@pytest.fixture
def h(bot):
    return IntegrationHelper(bot)
