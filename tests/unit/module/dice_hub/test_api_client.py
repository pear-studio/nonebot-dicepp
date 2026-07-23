"""
HubAPIClient._request 重试与错误处理契约测试

mock 边界：aiohttp.ClientSession，验证 _request 的重试、退避和错误升华。
"""
import asyncio
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from plugins.DicePP.module.dice_hub.api_client import HubAPIClient, HubAPIError


def _make_response(status=200, json_data=None):
    """创建 mock aiohttp response。"""
    resp = AsyncMock()
    resp.status = status
    resp.json = AsyncMock(return_value=json_data or {"status": "ok"})
    return resp


def _make_response_cm(response):
    """创建 response 的 async context manager。"""
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=response)
    cm.__aexit__ = AsyncMock(return_value=None)
    return cm


def _make_session(response_cm):
    """创建 mock aiohttp.ClientSession。"""
    session = MagicMock()
    # session.request 返回普通的 MagicMock（async context manager），不是 coroutine
    session.request = MagicMock(return_value=response_cm)
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    return session


@pytest.fixture
def client():
    return HubAPIClient("https://hub.example.com", "sk-test-key")


class TestHubAPIClientRequest:
    """_request 基本行为"""

    @pytest.mark.asyncio
    async def test_request_success_returns_json(self, client):
        resp = _make_response(200, {"status": "ok"})
        cm = _make_response_cm(resp)
        session = _make_session(cm)

        with patch('plugins.DicePP.module.dice_hub.api_client.aiohttp.ClientSession',
                   return_value=session):
            result = await client._request("POST", "/api/bots/register/", {"bot_id": "b1"})

        assert result == {"status": "ok"}

    @pytest.mark.asyncio
    async def test_request_passes_headers_and_data(self, client):
        resp = _make_response(200, {"status": "ok"})
        cm = _make_response_cm(resp)
        session = _make_session(cm)

        with patch('plugins.DicePP.module.dice_hub.api_client.aiohttp.ClientSession',
                   return_value=session):
            await client._request("POST", "/api/bots/register/", {"bot_id": "b1"})

        # 验证 session.request 被正确调用
        session.request.assert_called_once_with(
            "POST",
            "https://hub.example.com/api/bots/register/",
            json={"bot_id": "b1"},
            headers={"Content-Type": "application/json", "X-API-Key": "sk-test-key"},
        )

    @pytest.mark.asyncio
    async def test_request_http_error_raises_hub_api_error(self, client):
        """HTTP >= 400 应升华为 HubAPIError 并携带 status_code。"""
        resp = _make_response(404, {"error": "Not found"})
        cm = _make_response_cm(resp)
        session = _make_session(cm)

        with patch('plugins.DicePP.module.dice_hub.api_client.aiohttp.ClientSession',
                   return_value=session), \
             patch("asyncio.sleep"):  # 跳过退避
            with pytest.raises(HubAPIError) as exc_info:
                await client._request("GET", "/api/bots/me/")

        assert exc_info.value.status_code == 404
        assert "Not found" in str(exc_info.value)


class TestHubAPIClientRetry:
    """_request 自动重试与指数退避"""

    @pytest.mark.asyncio
    async def test_client_error_triggers_retry(self, client):
        """ClientError 触发重试，最终成功。"""
        import aiohttp

        call_count = 0

        def request_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise aiohttp.ClientError("connection refused")
            resp = _make_response(200, {"status": "ok"})
            return _make_response_cm(resp)

        session = MagicMock()
        session.request = MagicMock(side_effect=request_side_effect)
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=None)

        with patch('plugins.DicePP.module.dice_hub.api_client.aiohttp.ClientSession',
                   return_value=session), \
             patch("asyncio.sleep"):
            result = await client._request("GET", "/api/bots/")

        assert result == {"status": "ok"}
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_all_retries_exhausted_raises_hub_api_error(self, client):
        """所有重试耗尽后抛出 HubAPIError。"""
        import aiohttp

        session = MagicMock()
        session.request = MagicMock(side_effect=aiohttp.ClientError("connection refused"))
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=None)

        with patch('plugins.DicePP.module.dice_hub.api_client.aiohttp.ClientSession',
                   return_value=session), \
             patch("asyncio.sleep"):
            with pytest.raises(HubAPIError, match="Request failed"):
                await client._request("GET", "/api/bots/")

    @pytest.mark.asyncio
    async def test_retry_uses_exponential_backoff(self, client):
        """每次重试使用递增进度的退避 (1s, 2s, ...)。"""
        import aiohttp

        session = MagicMock()
        session.request = MagicMock(side_effect=aiohttp.ClientError("timeout"))
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=None)

        sleeps = []

        async def tracking_sleep(seconds):
            sleeps.append(seconds)

        with patch('plugins.DicePP.module.dice_hub.api_client.aiohttp.ClientSession',
                   return_value=session), \
             patch("asyncio.sleep", tracking_sleep):
            with pytest.raises(HubAPIError):
                await client._request("GET", "/api/bots/")

        # DEFAULT_RETRY=3 → 最多 2 次退避 (attempt 0→1, 1→2)
        assert len(sleeps) == 2
        assert sleeps[0] == 1  # (attempt=0) * 1 = 1
        assert sleeps[1] == 2  # (attempt=1) * 1 = 2

    @pytest.mark.asyncio
    async def test_retry_count_default_is_three(self):
        """验证默认重试次数为 3。"""
        from plugins.DicePP.module.dice_hub.api_client import DEFAULT_RETRY
        assert DEFAULT_RETRY == 3
