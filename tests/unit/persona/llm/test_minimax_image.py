"""MiniMaxImageProvider 单元测试 — classify_error 错误码 2013 细分"""
import asyncio
import pytest
from unittest.mock import patch, AsyncMock, MagicMock

from plugins.DicePP.module.persona.llm.errors import ErrorKind
from plugins.DicePP.module.persona.llm.providers.minimax_image import MiniMaxImageProvider
from plugins.DicePP.module.persona.llm.providers.protocol import ErrorClass


class TestClassifyError2013:
    """错误码 2013 的细分分类"""

    def test_invalid_params_prompt_length_is_retryable(self):
        """参数错误：prompt 过长 — 来自真实 API 返回"""
        e = RuntimeError(
            "image gen API error [2013]: invalid params, prompt length must be less than 1500"
        )
        assert MiniMaxImageProvider.classify_error(e) == ErrorClass.RETRYABLE

    def test_invalid_params_chat_setting_is_retryable(self):
        """参数错误：chat setting — 来自历史部署排障案例"""
        e = RuntimeError(
            "image gen API error [2013]: invalid params, invalid chat setting"
        )
        assert MiniMaxImageProvider.classify_error(e) == ErrorClass.RETRYABLE

    def test_content_moderation_is_non_retryable(self):
        """内容审核不通过 — 不可重试"""
        e = RuntimeError(
            "image gen API error [2013]: content moderation failed"
        )
        assert MiniMaxImageProvider.classify_error(e) == ErrorClass.NON_RETRYABLE


class TestClassifyErrorOtherCodes:
    """其他错误码回归保护"""

    @pytest.mark.parametrize("code", [1001, 1002, 1004, 1008, 2056])
    def test_known_non_retryable_codes(self, code):
        e = RuntimeError(f"image gen API error [{code}]: some error")
        assert MiniMaxImageProvider.classify_error(e) == ErrorClass.NON_RETRYABLE

    def test_unknown_code_is_retryable(self):
        e = RuntimeError("image gen API error [9999]: unknown error")
        assert MiniMaxImageProvider.classify_error(e) == ErrorClass.RETRYABLE


class TestClassifyErrorKind:
    """classify_error_kind 细粒度错误分类"""

    def test_1026_content_filtered(self):
        e = RuntimeError("image gen API error [1026]: input new_sensitive")
        assert MiniMaxImageProvider.classify_error_kind(e) == ErrorKind.CONTENT_FILTERED

    def test_1027_content_filtered(self):
        e = RuntimeError("image gen API error [1027]: output content error")
        assert MiniMaxImageProvider.classify_error_kind(e) == ErrorKind.CONTENT_FILTERED

    def test_new_sensitive_keyword(self):
        e = RuntimeError("image gen failed: new_sensitive check")
        assert MiniMaxImageProvider.classify_error_kind(e) == ErrorKind.CONTENT_FILTERED

    def test_2013_content_moderation(self):
        """2013 内容审核 → CONTENT_FILTERED"""
        e = RuntimeError("image gen API error [2013]: content moderation failed")
        assert MiniMaxImageProvider.classify_error_kind(e) == ErrorKind.CONTENT_FILTERED

    def test_2013_invalid_params_not_content_filtered(self):
        """2013 参数错误不误杀为内容过滤"""
        e = RuntimeError("image gen API error [2013]: invalid params, prompt too long")
        assert MiniMaxImageProvider.classify_error_kind(e) is None

    def test_unknown_error_not_content_filtered(self):
        e = RuntimeError("image gen API error [9999]: unknown error")
        assert MiniMaxImageProvider.classify_error_kind(e) is None


# ── Q167: generate_image / probe ──────────────────────────────────────────────


class _MockResponse:
    """模拟 httpx.Response，支持 .json() 方法。"""
    def __init__(self, status_code: int, json_data: dict):
        self.status_code = status_code
        self._json_data = json_data
        self.text = str(json_data)

    def json(self):
        return self._json_data


class TestGenerateImage:
    """MiniMaxImageProvider.generate_image 请求构造与响应解析"""

    @pytest.fixture
    def provider(self):
        return MiniMaxImageProvider(
            api_key="test-key",
            base_url="https://api.minimaxi.com",
            model="image-01",
        )

    @pytest.mark.asyncio
    async def test_generate_image_success_list_format(self, provider):
        """正常返回（data 为 list 格式）→ 返回图片 URL"""
        mock_resp = _MockResponse(200, {
            "base_resp": {"status_code": 0, "status_msg": ""},
            "data": [{"url": "https://example.com/img.png"}],
        })

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            mock_client.post = AsyncMock(return_value=mock_resp)

            url = await provider.generate_image("a cute cat")

        assert url == "https://example.com/img.png"
        # 验证请求参数
        mock_client.post.assert_called_once()
        _, kwargs = mock_client.post.call_args
        assert kwargs["json"]["model"] == "image-01"
        assert kwargs["json"]["prompt"] == "a cute cat"
        assert kwargs["json"]["n"] == 1

    @pytest.mark.asyncio
    async def test_generate_image_success_dict_format(self, provider):
        """正常返回（data 为 dict 格式，含 image_urls）→ 返回图片 URL"""
        mock_resp = _MockResponse(200, {
            "base_resp": {"status_code": 0, "status_msg": ""},
            "data": {"image_urls": ["https://example.com/img.png"]},
        })

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            mock_client.post = AsyncMock(return_value=mock_resp)

            url = await provider.generate_image("a cute cat")

        assert url == "https://example.com/img.png"

    @pytest.mark.asyncio
    async def test_generate_image_api_error_raises(self, provider):
        """API 返回 base_resp.status_code != 0 → RuntimeError"""
        mock_resp = _MockResponse(200, {
            "base_resp": {"status_code": 2013, "status_msg": "invalid params"},
            "data": None,
        })

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            mock_client.post = AsyncMock(return_value=mock_resp)

            with pytest.raises(RuntimeError, match="2013"):
                await provider.generate_image("bad prompt")

    @pytest.mark.asyncio
    async def test_generate_image_http_error_raises(self, provider):
        """HTTP 非 200 → RuntimeError"""
        mock_resp = _MockResponse(401, {})
        mock_resp.text = "Unauthorized"

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            mock_client.post = AsyncMock(return_value=mock_resp)

            with pytest.raises(RuntimeError, match="401"):
                await provider.generate_image("test")

    @pytest.mark.asyncio
    async def test_generate_image_empty_result_raises(self, provider):
        """API 返回空 data → RuntimeError"""
        mock_resp = _MockResponse(200, {
            "base_resp": {"status_code": 0, "status_msg": ""},
            "data": [],
        })

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            mock_client.post = AsyncMock(return_value=mock_resp)

            with pytest.raises(RuntimeError, match="空结果"):
                await provider.generate_image("test")

    @pytest.mark.asyncio
    async def test_generate_image_timeout_raises(self, provider):
        """HTTP 超时 → asyncio.TimeoutError"""
        from httpx import TimeoutException
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            mock_client.post = AsyncMock(side_effect=TimeoutException("timeout"))

            with pytest.raises(asyncio.TimeoutError):
                await provider.generate_image("test")

    @pytest.mark.asyncio
    async def test_generate_image_http_transport_error_raises(self, provider):
        """HTTP 传输错误 → RuntimeError"""
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            from httpx import HTTPError
            mock_client.post = AsyncMock(side_effect=HTTPError("connection failed"))

            with pytest.raises(RuntimeError, match="HTTP error"):
                await provider.generate_image("test")


class TestProbe:
    """MiniMaxImageProvider.probe 方法"""

    @pytest.fixture
    def provider(self):
        return MiniMaxImageProvider(
            api_key="test-key",
            base_url="https://api.minimaxi.com",
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status_code", [200, 400, 401, 403, 404, 422])
    async def test_probe_returns_true_for_valid_status(self, provider, status_code):
        """probe 对 200/400/401/403/404/422 返回 True"""
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            mock_client.post = AsyncMock(return_value=_MockResponse(status_code, {}))

            result = await provider.probe()
        assert result is True

    @pytest.mark.asyncio
    async def test_probe_returns_false_for_500(self, provider):
        """probe 对 500 返回 False（不在白名单中）"""
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            mock_client.post = AsyncMock(return_value=_MockResponse(500, {}))

            result = await provider.probe()
        assert result is False

    @pytest.mark.asyncio
    async def test_probe_returns_false_on_exception(self, provider):
        """probe 在网络异常时返回 False（不抛异常）"""
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            mock_client.post = AsyncMock(side_effect=RuntimeError("network error"))

            result = await provider.probe()
        assert result is False
