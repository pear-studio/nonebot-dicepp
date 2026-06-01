"""图片处理工具函数单元测试 — 纯逻辑，无 LLM / DB 依赖"""
import json
import os
import tempfile
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from plugins.DicePP.module.persona.agent.loop import _embed_images_in_messages
from plugins.DicePP.module.persona.agent.runtime import _build_image_content_parts
from plugins.DicePP.module.persona.chat.context import _build_image_markers, _safe_estimate_tokens
from plugins.DicePP.module.persona.image_cache import ImageCache


# ── _build_image_content_parts ────────────────────────────────────────────────


class TestBuildImageContentParts:
    """_build_image_content_parts: text + data_urls → List[dict] parts"""

    def test_text_only(self):
        parts = _build_image_content_parts("hello", [])
        assert parts == [{"type": "text", "text": "hello"}]

    def test_text_with_one_image(self):
        parts = _build_image_content_parts("hi", ["data:image/png;base64,abc"])
        assert len(parts) == 2
        assert parts[0] == {"type": "text", "text": "hi"}
        assert parts[1] == {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}}

    def test_text_with_multiple_images(self):
        urls = ["url1", "url2", "url3"]
        parts = _build_image_content_parts("msg", urls)
        assert len(parts) == 4
        assert parts[0]["type"] == "text"
        for i, url in enumerate(urls):
            assert parts[i + 1] == {"type": "image_url", "image_url": {"url": url}}

    def test_empty_text(self):
        parts = _build_image_content_parts("", ["url1"])
        assert parts[0] == {"type": "text", "text": ""}


# ── _embed_images_in_messages ─────────────────────────────────────────────────


class TestEmbedImagesInMessages:
    """_embed_images_in_messages: [图片 #n] 标记替换为多模态 parts"""

    def test_no_markers_unchanged(self):
        messages = [{"role": "user", "content": "hello world"}]
        result = _embed_images_in_messages(messages, {1: "url1"})
        assert result == messages

    def test_single_image_replace(self):
        messages = [{"role": "user", "content": "看看 [图片 #1] 吧"}]
        result = _embed_images_in_messages(messages, {1: "data_url_1"})
        assert len(result) == 1
        parts = result[0]["content"]
        assert isinstance(parts, list)
        assert len(parts) == 3
        assert parts[0] == {"type": "text", "text": "看看 "}
        assert parts[1] == {"type": "image_url", "image_url": {"url": "data_url_1"}}
        assert parts[2] == {"type": "text", "text": " 吧"}

    def test_multiple_images_ordered(self):
        messages = [{"role": "user", "content": "[图片 #1] 和 [图片 #2]"}]
        data_urls = {1: "url_a", 2: "url_b"}
        result = _embed_images_in_messages(messages, data_urls)
        parts = result[0]["content"]
        # 按 dict key 排序：先 #1 再 #2，marker 在开头时空 text 被跳过
        assert parts[0] == {"type": "image_url", "image_url": {"url": "url_a"}}
        assert parts[1] == {"type": "text", "text": " 和 "}
        assert parts[2] == {"type": "image_url", "image_url": {"url": "url_b"}}

    def test_emoji_marker_kept_as_text(self):
        """[表情 #n] 标记不替换，保留纯文本"""
        messages = [{"role": "user", "content": "看看 [表情 #1]"}]
        result = _embed_images_in_messages(messages, {})
        # 没有 [图片 #] 标记，原样返回
        assert result[0]["content"] == "看看 [表情 #1]"

    def test_non_string_content_skipped(self):
        messages = [{"role": "user", "content": [{"type": "text", "text": "already parts"}]}]
        result = _embed_images_in_messages(messages, {1: "url"})
        assert result == messages

    def test_empty_content(self):
        messages = [{"role": "user", "content": ""}]
        result = _embed_images_in_messages(messages, {1: "url"})
        assert result == messages

    def test_no_match_marker_not_in_data(self):
        """标记存在但 data_urls 中没有对应 key → 标记保留为文本"""
        messages = [{"role": "user", "content": "[图片 #5]"}]
        result = _embed_images_in_messages(messages, {1: "url"})
        # #5 不在 {1: url} 中，标记不会被替换
        parts = result[0]["content"]
        assert isinstance(parts, list)
        # 整个文本保留
        assert parts[0]["text"] == "[图片 #5]"

    def test_preserves_other_messages(self):
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "[图片 #1]"},
            {"role": "assistant", "content": "ok"},
        ]
        result = _embed_images_in_messages(messages, {1: "url"})
        assert result[0]["content"] == "sys"  # unchanged
        assert isinstance(result[1]["content"], list)  # replaced
        assert result[2]["content"] == "ok"  # unchanged


# ── _build_image_markers ─────────────────────────────────────────────────────


class TestBuildImageMarkers:
    """_build_image_markers: 为含图片的历史消息构建标记前缀"""

    def test_no_indices(self):
        msg = {"content": "hello", "image_meta": [{"sub_type": "0"}]}
        assert _build_image_markers(msg) == ""

    def test_no_image_meta(self):
        msg = {"content": "hello", "_img_indices": [1]}
        assert _build_image_markers(msg) == ""

    def test_single_image(self):
        msg = {
            "_img_indices": [1],
            "image_meta": [{"sub_type": "0"}],
        }
        assert _build_image_markers(msg) == "[图片 #1] "

    def test_single_emoji(self):
        msg = {
            "_img_indices": [1],
            "image_meta": [{"sub_type": "1"}],
        }
        assert _build_image_markers(msg) == "[表情 #1] "

    def test_multiple_images(self):
        msg = {
            "_img_indices": [1, 2],
            "image_meta": [{"sub_type": "0"}, {"sub_type": "1"}],
        }
        assert _build_image_markers(msg) == "[图片 #1][表情 #2] "

    def test_indices_longer_than_meta(self):
        """_img_indices 比 image_meta 长时，超出部分默认为 [图片]"""
        msg = {
            "_img_indices": [1, 2, 3],
            "image_meta": [{"sub_type": "0"}],
        }
        result = _build_image_markers(msg)
        assert "[图片 #1]" in result
        assert "[图片 #2]" in result
        assert "[图片 #3]" in result

    def test_empty_msg(self):
        assert _build_image_markers({}) == ""


# ── _safe_estimate_tokens ────────────────────────────────────────────────────


class TestSafeEstimateTokens:
    """_safe_estimate_tokens: str / List[dict] content 防御性估算"""

    def test_string_content(self):
        result = _safe_estimate_tokens("hello world")
        assert result > 0

    def test_list_with_text_parts(self):
        parts = [
            {"type": "text", "text": "hello world"},
            {"type": "image_url", "image_url": {"url": "data:..."}},
        ]
        result = _safe_estimate_tokens(parts)
        # image_url part 没有 text key → get("text", "") = "" → 0 tokens
        # text part 有 tokens
        assert result > 0

    def test_list_with_non_dict_elements(self):
        parts = ["not a dict", {"type": "text", "text": "hello"}]
        result = _safe_estimate_tokens(parts)
        # "not a dict" 被 isinstance(p, dict) 过滤
        assert result > 0

    def test_empty_list(self):
        assert _safe_estimate_tokens([]) == 0.0

    def test_none_returns_zero(self):
        assert _safe_estimate_tokens(None) == 0.0

    def test_numeric_returns_zero(self):
        assert _safe_estimate_tokens(42) == 0.0


# ── ImageCache ───────────────────────────────────────────────────────────────


class TestImageCache:
    """ImageCache: 下载、缓存、读取、删除（mock HTTP + 文件 I/O）"""

    @pytest.fixture
    def cache_dir(self, tmp_path):
        d = tmp_path / "persona_images"
        d.mkdir()
        return d

    @pytest.fixture
    def cache(self, cache_dir):
        c = ImageCache()
        c.IMAGE_DIR = str(cache_dir)
        return c

    def test_read_cache_hit(self, cache, cache_dir):
        fpath = cache_dir / "abcd1234.b64"
        fpath.write_text("data:image/png;base64,abc")
        assert cache.read_cache("abcd1234") == "data:image/png;base64,abc"

    def test_read_cache_miss(self, cache):
        assert cache.read_cache("nonexistent") is None

    def test_delete_cache(self, cache, cache_dir):
        fpath = cache_dir / "abcd1234.b64"
        fpath.write_text("data")
        cache.delete_cache("abcd1234")
        assert not fpath.exists()

    def test_delete_cache_missing(self, cache):
        # 不应抛异常
        cache.delete_cache("nonexistent")

    @pytest.mark.asyncio
    async def test_download_and_cache_skips_emoji(self, cache):
        meta = [{"url": "http://example.com/emoji.png", "sub_type": "1", "cache_hash": None}]
        await cache.download_and_cache(meta)
        assert meta[0].get("cache_hash") is None

    @pytest.mark.asyncio
    async def test_download_and_cache_skips_already_cached(self, cache, cache_dir):
        fpath = cache_dir / "existing.b64"
        fpath.write_text("data:image/png;base64,old")
        meta = [{"url": "http://example.com/img.png", "cache_hash": "existing", "sub_type": "0"}]
        await cache.download_and_cache(meta)
        # 已有 cache_hash，不应重新下载
        assert meta[0]["cache_hash"] == "existing"

    @pytest.mark.asyncio
    async def test_download_and_cache_success(self, cache, cache_dir):
        fake_content = b"\x89PNG\r\n"
        fake_response = MagicMock()
        fake_response.status_code = 200
        fake_response.content = fake_content
        fake_response.headers = {"content-type": "image/png"}

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=fake_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("plugins.DicePP.module.persona.image_cache.httpx.AsyncClient", return_value=mock_client):
            meta = [{"url": "http://example.com/img.png", "sub_type": "0", "cache_hash": None}]
            await cache.download_and_cache(meta)

        assert meta[0]["cache_hash"] is not None
        assert meta[0]["size"] == len(fake_content)
        # 验证文件写入
        h = meta[0]["cache_hash"]
        assert (cache_dir / f"{h}.b64").exists()

    @pytest.mark.asyncio
    async def test_download_and_cache_empty_url(self, cache):
        meta = [{"url": "", "sub_type": "0", "cache_hash": None}]
        await cache.download_and_cache(meta)
        assert meta[0].get("cache_hash") is None

    @pytest.mark.asyncio
    async def test_download_and_cache_http_error(self, cache):
        fake_response = MagicMock()
        fake_response.status_code = 404

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=fake_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("plugins.DicePP.module.persona.image_cache.httpx.AsyncClient", return_value=mock_client):
            meta = [{"url": "http://example.com/missing.png", "sub_type": "0", "cache_hash": None}]
            await cache.download_and_cache(meta)

        assert meta[0].get("cache_hash") is None
