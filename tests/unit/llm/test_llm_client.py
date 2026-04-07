"""
单元测试: SimpleLLMClient
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import sys
sys.path.insert(0, "src")

from plugins.DicePP.module.llm.client import SimpleLLMClient


class TestSimpleLLMClient:
    """测试 SimpleLLMClient"""

    @pytest.fixture
    def mock_openai(self):
        """Mock OpenAI 客户端"""
        with patch("plugins.DicePP.module.llm.client.AsyncOpenAI") as MockClient:
            mock_client = MagicMock()
            MockClient.return_value = mock_client
            yield mock_client

    @pytest.mark.asyncio
    async def test_chat_success(self, mock_openai):
        """测试正常对话"""
        # 设置 mock 返回值
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "你好！有什么可以帮助你的吗？"
        mock_openai.chat.completions.create = AsyncMock(return_value=mock_response)

        # 创建客户端
        client = SimpleLLMClient(
            api_key="test-key",
            base_url="https://test.com/v1",
            model="test-model"
        )

        # 调用对话
        messages = [{"role": "user", "content": "你好"}]
        response = await client.chat(messages, timeout=10)

        # 验证结果
        assert response == "你好！有什么可以帮助你的吗？"
        mock_openai.chat.completions.create.assert_called_once()

    @pytest.mark.asyncio
    async def test_chat_timeout(self, mock_openai):
        """测试超时处理"""
        # 设置 mock 模拟超时
        async def slow_response(*args, **kwargs):
            await asyncio.sleep(100)  # 长时间等待
            return None

        mock_openai.chat.completions.create = slow_response

        # 创建客户端
        client = SimpleLLMClient(
            api_key="test-key",
            base_url="https://test.com/v1",
            model="test-model"
        )

        # 调用对话（设置短超时）
        messages = [{"role": "user", "content": "你好"}]
        response = await client.chat(messages, timeout=0.01)

        # 验证超时返回友好提示
        assert "超时" in response or "思考超时" in response

    @pytest.mark.asyncio
    async def test_chat_error(self, mock_openai):
        """测试错误处理"""
        # 设置 mock 抛出异常
        mock_openai.chat.completions.create = AsyncMock(side_effect=Exception("API Error"))

        # 创建客户端
        client = SimpleLLMClient(
            api_key="test-key",
            base_url="https://test.com/v1",
            model="test-model"
        )

        # 调用对话
        messages = [{"role": "user", "content": "你好"}]
        response = await client.chat(messages, timeout=10)

        # 验证返回友好错误（不是抛出异常）
        assert "出错" in response or "暂时不可用" in response

    def test_init_without_api_key(self, mock_openai):
        """测试初始化时没有 API Key"""
        # 创建客户端（应该成功创建，但后续调用会失败）
        client = SimpleLLMClient(
            api_key="",
            base_url="https://test.com/v1",
            model="test-model"
        )

        assert client.model == "test-model"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
