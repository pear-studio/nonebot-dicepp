"""Fixture registration for core command integration tests."""

from __future__ import annotations

import pytest

from tests.support.bot import async_make_test_bot, async_teardown_test_bot
from tests.support.core_command import IntegrationHelper


@pytest.fixture(scope="module")
async def bot():
    """每个测试模块一个独立 Bot（独立 DB、配置与数据目录）。

    指令集成测试允许同一文件内的测试按顺序共享 Bot 状态（例如欢迎词
    设置-验证-关闭-重置流程），但跨文件必须隔离：共享 Bot 会让群配置、
    欢迎词等可变状态经共享群/用户键空间泄漏到后续文件。module 边界
    整体重建 Bot 覆盖 Bot 实例持有的全部可变状态（全部 DB 表、配置、
    缓存），无需逐表枚举清理。
    """
    bot_instance, _proxy = await async_make_test_bot("test_cmd")
    yield bot_instance
    await async_teardown_test_bot(bot_instance)


@pytest.fixture
def h(bot):
    return IntegrationHelper(bot)
