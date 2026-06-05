"""图片处理工具函数单元测试 — 纯逻辑，无 LLM / DB 依赖"""
import json
import os
import tempfile
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

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


# ── _build_image_markers ─────────────────────────────────────────────────────


class TestBuildImageMarkers:
    """_build_image_markers: 为含图片的历史消息构建 [图片 <hash>] 标记前缀"""

    def test_no_image_meta(self):
        msg = {"content": "hello"}
        assert _build_image_markers(msg) == ""

    def test_single_image(self):
        msg = {
            "image_meta": [{"sub_type": "0", "image_hash": "abc12345"}],
        }
        assert _build_image_markers(msg) == "[图片 abc12345] "

    def test_single_emoji(self):
        msg = {
            "image_meta": [{"sub_type": "1", "image_hash": "deadbeef"}],
        }
        assert _build_image_markers(msg) == "[表情 deadbeef] "

    def test_multiple_images(self):
        msg = {
            "image_meta": [
                {"sub_type": "0", "image_hash": "aaa11111"},
                {"sub_type": "1", "image_hash": "bbb22222"},
            ],
        }
        assert _build_image_markers(msg) == "[图片 aaa11111][表情 bbb22222] "

    def test_old_data_no_image_hash_computed_on_the_fly(self):
        """存量数据无 image_hash 时用 url/file 现场计算"""
        msg = {
            "image_meta": [{"sub_type": "0", "url": "http://example.com/img.png"}],
        }
        result = _build_image_markers(msg)
        # 应有 [图片 <8位hex>] 格式
        assert result.startswith("[图片 ")
        assert result.endswith("] ")
        # hash 是 8 位 hex
        hash_part = result[4:12]
        assert len(hash_part) == 8
        assert all(c in "0123456789abcdef" for c in hash_part)

    def test_entry_missing_url_and_file_skipped_with_warning(self):
        """url 和 file 都为空时跳过该图片标记"""
        msg = {
            "image_meta": [
                {"sub_type": "0", "url": "", "file": ""},
                {"sub_type": "0", "image_hash": "ccc33333"},
            ],
        }
        result = _build_image_markers(msg)
        # 第一个 entry 被跳过，只有第二个的标记
        assert result == "[图片 ccc33333] "

    def test_empty_msg(self):
        assert _build_image_markers({}) == ""


# ── ImageCache.compute_image_hash ─────────────────────────────────────────────


class TestComputeImageHash:
    """ImageCache.compute_image_hash: url 优先、file 兜底、空输入返回 None"""

    def test_url_priority(self):
        entry = {"url": "http://example.com/a.png", "file": "b.png"}
        h = ImageCache.compute_image_hash(entry)
        assert h == ImageCache.compute_image_hash({"url": "http://example.com/a.png"})

    def test_file_fallback(self):
        entry = {"file": "abc.png"}
        h = ImageCache.compute_image_hash(entry)
        assert h is not None
        assert len(h) == 8

    def test_both_empty_returns_none(self):
        assert ImageCache.compute_image_hash({"url": "", "file": ""}) is None

    def test_empty_dict_returns_none(self):
        assert ImageCache.compute_image_hash({}) is None

    def test_deterministic(self):
        entry = {"url": "http://example.com/same.png"}
        h1 = ImageCache.compute_image_hash(entry)
        h2 = ImageCache.compute_image_hash(entry)
        assert h1 == h2


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
        assert result > 0

    def test_list_with_non_dict_elements(self):
        parts = ["not a dict", {"type": "text", "text": "hello"}]
        result = _safe_estimate_tokens(parts)
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
        assert meta[0].get("download_status") == "skipped_emoji"

    @pytest.mark.asyncio
    async def test_download_and_cache_force_emoji_downloads(self, cache, cache_dir):
        """force_emoji=True 时表情按需下载（供 LLM 工具主动请求）"""
        fake_content = b"\x89PNG\r\n"
        fake_response = MagicMock()
        fake_response.status_code = 200
        fake_response.content = fake_content
        fake_response.headers = {"content-type": "image/gif"}

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=fake_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("plugins.DicePP.module.persona.image_cache.httpx.AsyncClient", return_value=mock_client):
            meta = [{"url": "http://example.com/emoji.png", "sub_type": "1", "cache_hash": None}]
            await cache.download_and_cache(meta, force_emoji=True)

        assert meta[0].get("cache_hash") is not None
        assert meta[0].get("download_status") != "skipped_emoji"
        assert (cache_dir / f"{meta[0]['cache_hash']}.b64").exists()

    @pytest.mark.asyncio
    async def test_download_and_cache_skips_already_cached(self, cache, cache_dir):
        fpath = cache_dir / "existing.b64"
        fpath.write_text("data:image/png;base64,old")
        meta = [{"url": "http://example.com/img.png", "cache_hash": "existing", "sub_type": "0"}]
        await cache.download_and_cache(meta)
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
