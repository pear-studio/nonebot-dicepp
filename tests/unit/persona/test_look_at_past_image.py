"""look_at_past_image 工具测试 — 表情按需下载 / 普通图片缓存命中"""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from plugins.DicePP.module.persona.tools.context import ToolContext
from plugins.DicePP.module.persona.tools.look_at_past_image import look_at_past_image_executor


def _make_ctx(store, image_cache):
    return ToolContext(user_id="u1", group_id="g1", store=store, send=None)


@pytest.mark.asyncio
async def test_emoji_can_now_be_viewed_on_demand():
    """sub_type=1 表情默认不入库，工具调用时按需下载并返回"""
    image_cache = MagicMock()
    image_cache.read_cache.return_value = None  # 缓存未命中

    target = {
        "url": "http://example.com/emoji.png",
        "sub_type": "1",
        "cache_hash": None,
    }

    async def fake_download(meta, *, force_emoji=False):
        assert force_emoji is True, "表情必须 force_emoji=True 绕过默认跳过"
        meta[0]["cache_hash"] = "abc12345"
        meta[0]["size"] = 1024

    image_cache.download_and_cache = AsyncMock(side_effect=fake_download)
    image_cache.read_cache.side_effect = lambda h: f"data:image/gif;base64,FAKE" if h == "abc12345" else None

    store = MagicMock()
    store.get_recent_images = AsyncMock(return_value=[target])
    store.update_image_meta = AsyncMock()
    store.image_cache = image_cache

    ctx = _make_ctx(store, image_cache)
    result = json.loads(await look_at_past_image_executor({"image_index": 1}, ctx))

    assert "data_url" in result
    assert result["data_url"].startswith("data:image/")
    image_cache.download_and_cache.assert_awaited_once()
    call_kwargs = image_cache.download_and_cache.await_args.kwargs
    assert call_kwargs.get("force_emoji") is True


@pytest.mark.asyncio
async def test_regular_image_cache_hit_skips_download():
    """sub_type=0 普通图片缓存命中时不再下载"""
    image_cache = MagicMock()
    image_cache.read_cache.return_value = "data:image/png;base64,CACHED"

    target = {
        "url": "http://example.com/img.png",
        "sub_type": "0",
        "cache_hash": "existing",
    }
    store = MagicMock()
    store.get_recent_images = AsyncMock(return_value=[target])
    store.image_cache = image_cache

    ctx = _make_ctx(store, image_cache)
    result = json.loads(await look_at_past_image_executor({"image_index": 1}, ctx))

    assert result["data_url"] == "data:image/png;base64,CACHED"
    image_cache.download_and_cache.assert_not_called()


@pytest.mark.asyncio
async def test_emoji_download_failure_returns_error():
    """表情 URL 失效时返回错误信息，不再是"该消息为表情"硬拒绝"""
    image_cache = MagicMock()
    image_cache.read_cache.return_value = None

    target = {
        "url": "http://example.com/expired.png",
        "sub_type": "1",
        "cache_hash": None,
    }

    async def fake_download(meta, *, force_emoji=False):
        pass  # 模拟下载失败：cache_hash 仍为 None

    image_cache.download_and_cache = AsyncMock(side_effect=fake_download)

    store = MagicMock()
    store.get_recent_images = AsyncMock(return_value=[target])
    store.image_cache = image_cache

    ctx = _make_ctx(store, image_cache)
    result = json.loads(await look_at_past_image_executor({"image_index": 1}, ctx))

    assert "error" in result
    assert "表情" not in result["error"]  # 不再是"该消息为表情"硬拒绝
    assert "URL" in result["error"]  # 是下载失败的语义
