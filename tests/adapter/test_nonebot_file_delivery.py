from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, call

import pytest

import adapter.nonebot_adapter as nonebot_adapter
from adapter.nonebot_adapter import NoneBotClientProxy, _group_folder_cache
from core.command import BotSendFileCommand, FileDeliveryOutcome
from core.communication import GroupMessagePort, PrivateMessagePort


@pytest.fixture(autouse=True)
def clear_folder_cache():
    snapshot = {
        group_id: dict(folders)
        for group_id, folders in _group_folder_cache.items()
    }
    _group_folder_cache.clear()
    yield
    _group_folder_cache.clear()
    _group_folder_cache.update(snapshot)


def _proxy():
    bot = MagicMock()
    bot.self_id = "42"
    bot.call_api = AsyncMock()
    bot.send_group_msg = AsyncMock()
    return NoneBotClientProxy(bot), bot


@pytest.mark.asyncio
async def test_folder_upload_success_returns_delivery_without_post_send_event(monkeypatch):
    proxy, bot = _proxy()
    post_send = AsyncMock()
    monkeypatch.setattr(nonebot_adapter, "_trigger_post_send_hooks", post_send)
    bot.call_api.side_effect = [
        {"folders": [{"folder_name": "跑团log", "folder_id": "folder-1"}]},
        None,
    ]
    target = GroupMessagePort("100")
    command = BotSendFileCommand("42", "C:/tmp/log.txt", "跑团log/log.txt", [target])

    result = await proxy.process_bot_command(command)

    assert len(result.file_deliveries) == 1
    delivery = result.file_deliveries[0]
    assert delivery.target is target
    assert delivery.outcome is FileDeliveryOutcome.FOLDER_SUCCESS
    assert delivery.requested_folder == "跑团log"
    assert delivery.succeeded is True
    assert bot.call_api.await_args_list[-1] == call(
        "upload_group_file",
        group_id=100,
        file="C:/tmp/log.txt",
        name="log.txt",
        folder="folder-1",
    )
    post_send.assert_not_awaited()


@pytest.mark.asyncio
async def test_direct_root_upload_success_returns_root_delivery():
    proxy, bot = _proxy()
    bot.call_api.return_value = None
    target = GroupMessagePort("101")
    command = BotSendFileCommand("42", "C:/tmp/log.txt", "log.txt", [target])

    result = await proxy.process_bot_command(command)

    delivery = result.file_deliveries[0]
    assert delivery.outcome is FileDeliveryOutcome.ROOT_SUCCESS
    assert delivery.requested_folder is None
    bot.call_api.assert_awaited_once_with(
        "upload_group_file",
        group_id=101,
        file="C:/tmp/log.txt",
        name="log.txt",
    )


@pytest.mark.asyncio
async def test_missing_folder_falls_back_without_post_send_event(monkeypatch):
    proxy, bot = _proxy()
    post_send = AsyncMock()
    monkeypatch.setattr(nonebot_adapter, "_trigger_post_send_hooks", post_send)
    bot.call_api.side_effect = [{"folders": []}, None]
    command = BotSendFileCommand(
        "42",
        "C:/tmp/log.txt",
        "跑团log/log.txt",
        [GroupMessagePort("102")],
    )

    result = await proxy.process_bot_command(command)

    assert result.file_deliveries[0].outcome is FileDeliveryOutcome.ROOT_FALLBACK_SUCCESS
    assert bot.call_api.await_args_list[-1] == call(
        "upload_group_file",
        group_id=102,
        file="C:/tmp/log.txt",
        name="log.txt",
    )
    post_send.assert_not_awaited()


@pytest.mark.asyncio
async def test_folder_upload_failure_falls_back_to_root():
    proxy, bot = _proxy()
    bot.call_api.side_effect = [
        {"folders": [{"folder_name": "跑团log", "folder_id": "folder-1"}]},
        RuntimeError("folder upload failed"),
        None,
    ]
    command = BotSendFileCommand(
        "42",
        "C:/tmp/log.txt",
        "跑团log/log.txt",
        [GroupMessagePort("103")],
    )

    result = await proxy.process_bot_command(command)

    assert result.file_deliveries[0].outcome is FileDeliveryOutcome.ROOT_FALLBACK_SUCCESS
    assert bot.call_api.await_count == 3


@pytest.mark.asyncio
async def test_file_upload_failure_returns_failed_without_post_send_event(monkeypatch):
    proxy, bot = _proxy()
    post_send = AsyncMock()
    monkeypatch.setattr(nonebot_adapter, "_trigger_post_send_hooks", post_send)
    bot.call_api.side_effect = [
        {"folders": [{"folder_name": "跑团log", "folder_id": "folder-1"}]},
        RuntimeError("folder upload failed"),
        RuntimeError("root upload failed"),
    ]
    command = BotSendFileCommand(
        "42",
        "C:/tmp/log.txt",
        "跑团log/log.txt",
        [GroupMessagePort("104")],
    )

    result = await proxy.process_bot_command(command)

    delivery = result.file_deliveries[0]
    assert delivery.outcome is FileDeliveryOutcome.FAILED
    assert delivery.succeeded is False
    assert "folder upload failed" in (delivery.error or "")
    assert "root upload failed" in (delivery.error or "")
    bot.send_group_msg.assert_awaited_once_with(
        group_id=104,
        message="文件发送失败！",
    )
    post_send.assert_not_awaited()


@pytest.mark.asyncio
async def test_private_file_target_is_explicitly_unsupported():
    proxy, bot = _proxy()
    target = PrivateMessagePort("200")
    command = BotSendFileCommand("42", "C:/tmp/log.txt", "log.txt", [target])

    result = await proxy.process_bot_command(command)

    delivery = result.file_deliveries[0]
    assert delivery.target is target
    assert delivery.outcome is FileDeliveryOutcome.UNSUPPORTED
    assert delivery.succeeded is False
    bot.call_api.assert_not_awaited()
    bot.send_group_msg.assert_not_awaited()


@pytest.mark.asyncio
async def test_multiple_targets_are_delivered_independently_in_order():
    proxy, bot = _proxy()

    async def call_api(api: str, **kwargs):
        assert api == "upload_group_file"
        if kwargs["group_id"] == 302:
            raise RuntimeError("second target failed")
        return None

    bot.call_api.side_effect = call_api
    targets = [GroupMessagePort("301"), GroupMessagePort("302")]
    command = BotSendFileCommand("42", "C:/tmp/log.txt", "log.txt", targets)

    result = await proxy.process_bot_command(command)

    assert [item.target for item in result.file_deliveries] == targets
    assert [item.outcome for item in result.file_deliveries] == [
        FileDeliveryOutcome.ROOT_SUCCESS,
        FileDeliveryOutcome.FAILED,
    ]
    bot.send_group_msg.assert_awaited_once_with(
        group_id=302,
        message="文件发送失败！",
    )
