"""Fixture registration for full-Bot command integration tests."""

from __future__ import annotations

import asyncio
from typing import Optional
import uuid

import pytest

from core.bot import Bot
from tests.support.bot import TestProxy
from tests.support.core_command import IntegrationHelper
from tests.support.fs_utils import rmtree_retry


_bot_instance: Optional[Bot] = None
_data_path: Optional[str] = None


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
async def bot():
    global _bot_instance, _data_path
    _bot_instance = Bot(
        f"test_module_{uuid.uuid4().hex[:12]}",
        readonly=True,
        no_tick=True,
    )
    _bot_instance.config.master = ["test_master"]
    proxy = TestProxy()
    _bot_instance.set_client_proxy(proxy)
    await _bot_instance.delay_init_command()
    proxy.mute = True
    _bot_instance.loc_helper.load_localization()
    _bot_instance.loc_helper.load_chat()
    _data_path = _bot_instance.data_path
    yield _bot_instance
    try:
        await _bot_instance.shutdown_async()
    finally:
        if _data_path:
            rmtree_retry(_data_path)


@pytest.fixture
def h(bot):
    return IntegrationHelper(bot)
