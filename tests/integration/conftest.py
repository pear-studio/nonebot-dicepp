"""Fixtures shared by current-process integration tests."""

from __future__ import annotations

import pytest

from plugins.DicePP.core.bot import Bot
from tests.support.bot import TestProxy, new_test_account
from tests.support.fs_utils import rmtree_retry


@pytest.fixture(scope="class")
def shared_bot():
    test_bot = Bot(new_test_account("test_bot"), no_tick=True)
    test_bot.config.master = "test_master"
    test_proxy = TestProxy()
    test_bot.set_client_proxy(test_proxy)
    test_bot.delay_init_debug()
    test_proxy.mute = True
    yield test_bot
    test_bot.shutdown_debug()
    rmtree_retry(test_bot.data_path)


@pytest.fixture
def fresh_bot():
    test_bot = Bot(new_test_account("test_bot_fresh"), no_tick=True)
    test_bot.config.master = "test_master"
    test_proxy = TestProxy()
    test_bot.set_client_proxy(test_proxy)
    test_bot.delay_init_debug()
    test_proxy.mute = True
    yield test_bot, test_proxy
    test_bot.shutdown_debug()
    rmtree_retry(test_bot.data_path)
