"""
Phase 3: 工具调用集成测试

测试 _chat_with_tools 完整流程
"""

import pytest
import asyncio
from typing import List, Dict, Any
from unittest.mock import Mock, AsyncMock

from plugins.DicePP.module.persona.llm.client import LLMClient
from plugins.DicePP.module.persona.llm.router import LLMRouter


class MockLLMResponse:
    """模拟 LLM 响应"""

    def __init__(self, content: str = None, tool_calls: List[Dict] = None):
        self.content = content
        self.tool_calls = tool_calls or []


class MockToolCall:
    """模拟工具调用"""

    def __init__(self, id: str, name: str, arguments: str):
        self.id = id
        self.function = Mock()
        self.function.name = name
        self.function.arguments = arguments


class TestChatWithTools:
    """测试 chat_with_tools 完整流程"""

    @pytest.mark.asyncio
    async def test_no_tool_calls(self):
        """测试无需工具调用的普通对话"""
        client = LLMClient(
            api_key="test_key",
            base_url="https://api.test.com/v1",
            model="gpt-4o"
        )

        # Mock _get_client
        mock_openai_client = Mock()
        mock_response = Mock()
        mock_response.choices = [Mock()]
        mock_response.choices[0].message = Mock()
        mock_response.choices[0].message.content = "你好！很高兴见到你。"
        mock_response.choices[0].message.tool_calls = None
        mock_response.usage = Mock()
        mock_response.usage.prompt_tokens = 100
        mock_response.usage.completion_tokens = 20
        mock_response.usage.prompt_tokens_details = None

        mock_openai_client.chat.completions.create = AsyncMock(return_value=mock_response)
        client._client = mock_openai_client

        messages = [{"role": "user", "content": "你好"}]
        tools = [{
            "type": "function",
            "function": {
                "name": "test_tool",
                "description": "测试工具",
                "parameters": {"type": "object", "properties": {}}
            }
        }]

        content, metadata = await client.chat_with_tools(
            messages=messages,
            tools=tools,
            tool_executor=None,  # 无需工具调用
            max_tool_rounds=5,
            timeout=60
        )

        assert content == "你好！很高兴见到你。"
        assert metadata["tool_rounds"] == 0
        assert metadata["total_tool_calls"] == 0

    @pytest.mark.asyncio
    async def test_single_tool_call(self):
        """测试单次工具调用"""
        client = LLMClient(
            api_key="test_key",
            base_url="https://api.test.com/v1",
            model="gpt-4o"
        )

        # Mock tool executor
        async def mock_executor(tool_calls: List[Dict]) -> List[Dict]:
            return [{
                "tool_call_id": tc["id"],
                "content": f"工具 {tc['name']} 执行结果"
            } for tc in tool_calls]

        # 第一轮：返回 tool_calls
        mock_response1 = Mock()
        mock_response1.choices = [Mock()]
        mock_response1.choices[0].message = Mock()
        mock_response1.choices[0].message.content = ""
        mock_response1.choices[0].message.tool_calls = [
            MockToolCall("tc_1", "search_memory", '{"query": "猫"}')
        ]
        mock_response1.usage = Mock()
        mock_response1.usage.prompt_tokens = 100
        mock_response1.usage.completion_tokens = 30
        mock_response1.usage.prompt_tokens_details = None

        # 第二轮：返回最终结果
        mock_response2 = Mock()
        mock_response2.choices = [Mock()]
        mock_response2.choices[0].message = Mock()
        mock_response2.choices[0].message.content = "我记得你喜欢猫！"
        mock_response2.choices[0].message.tool_calls = None
        mock_response2.usage = Mock()
        mock_response2.usage.prompt_tokens = 150
        mock_response2.usage.completion_tokens = 25
        mock_response2.usage.prompt_tokens_details = None

        mock_openai_client = Mock()
        mock_openai_client.chat.completions.create = AsyncMock(
            side_effect=[mock_response1, mock_response2]
        )
        client._client = mock_openai_client

        messages = [{"role": "user", "content": "你记得我喜欢什么动物吗？"}]
        tools = [{
            "type": "function",
            "function": {
                "name": "search_memory",
                "description": "搜索记忆",
                "parameters": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}}
                }
            }
        }]

        content, metadata = await client.chat_with_tools(
            messages=messages,
            tools=tools,
            tool_executor=mock_executor,
            max_tool_rounds=5,
            timeout=60
        )

        assert content == "我记得你喜欢猫！"
        assert metadata["tool_rounds"] == 1
        assert metadata["total_tool_calls"] == 1
        assert "search_memory" in metadata["tool_names"]

    @pytest.mark.asyncio
    async def test_tool_executor_none_graceful_fallback(self):
        """测试 tool_executor 为 None 时优雅降级，将错误返回给 LLM 处理"""
        client = LLMClient(
            api_key="test_key",
            base_url="https://api.test.com/v1",
            model="gpt-4o"
        )

        # 第一轮：返回 tool_calls
        mock_response1 = Mock()
        mock_response1.choices = [Mock()]
        mock_response1.choices[0].message = Mock()
        mock_response1.choices[0].message.content = ""
        mock_response1.choices[0].message.tool_calls = [
            MockToolCall("tc_1", "search_memory", '{"query": "test"}')
        ]
        mock_response1.usage = None

        # 第二轮：LLM 生成最终回复（基于错误信息）
        mock_response2 = Mock()
        mock_response2.choices = [Mock()]
        mock_response2.choices[0].message = Mock()
        mock_response2.choices[0].message.content = "抱歉，我暂时无法搜索记忆，但我们还是聊聊吧。"
        mock_response2.choices[0].message.tool_calls = None
        mock_response2.usage = None

        mock_openai_client = Mock()
        mock_openai_client.chat.completions.create = AsyncMock(
            side_effect=[mock_response1, mock_response2]
        )
        client._client = mock_openai_client

        messages = [{"role": "user", "content": "test"}]
        tools = [{"type": "function", "function": {"name": "search_memory"}}]

        content, metadata = await client.chat_with_tools(
            messages=messages,
            tools=tools,
            tool_executor=None,  # 不提供 tool_executor
            max_tool_rounds=5,
            timeout=60
        )

        # 验证：LLM 被调用两次（第一次返回 tool_calls，第二次生成回复）
        assert mock_openai_client.chat.completions.create.call_count == 2
        # 验证：返回最终内容（不是错误消息）
        assert content == "抱歉，我暂时无法搜索记忆，但我们还是聊聊吧。"
        # 验证：元数据中包含工具调用轮次
        assert metadata["tool_rounds"] == 1

    @pytest.mark.asyncio
    async def test_tool_execution_failure(self):
        """测试工具执行失败处理"""
        client = LLMClient(
            api_key="test_key",
            base_url="https://api.test.com/v1",
            model="gpt-4o"
        )

        async def failing_executor(tool_calls: List[Dict]) -> List[Dict]:
            raise Exception("数据库连接失败")

        mock_response = Mock()
        mock_response.choices = [Mock()]
        mock_response.choices[0].message = Mock()
        mock_response.choices[0].message.content = ""
        mock_response.choices[0].message.tool_calls = [
            MockToolCall("tc_1", "search_memory", '{}')
        ]
        mock_response.usage = None

        mock_openai_client = Mock()
        mock_openai_client.chat.completions.create = AsyncMock(return_value=mock_response)
        client._client = mock_openai_client

        messages = [{"role": "user", "content": "test"}]
        tools = [{"type": "function", "function": {"name": "search_memory"}}]

        content, metadata = await client.chat_with_tools(
            messages=messages,
            tools=tools,
            tool_executor=failing_executor,
            max_tool_rounds=5,
            timeout=60
        )

        assert "工具执行失败" in content
        assert "数据库连接失败" in metadata["error"]

    @pytest.mark.asyncio
    async def test_max_tool_rounds_exceeded(self):
        """测试超过最大工具调用轮次"""
        client = LLMClient(
            api_key="test_key",
            base_url="https://api.test.com/v1",
            model="gpt-4o"
        )

        call_count = [0]

        def create_mock_response(**kwargs):
            call_count[0] += 1
            mock_response = Mock()
            mock_response.choices = [Mock()]
            mock_response.choices[0].message = Mock()
            mock_response.choices[0].message.content = ""
            mock_response.choices[0].message.tool_calls = [
                MockToolCall(f"tc_{call_count[0]}", "search_memory", '{}')
            ]
            mock_response.usage = None
            return mock_response

        async def mock_executor(tool_calls: List[Dict]) -> List[Dict]:
            return [{"tool_call_id": tc["id"], "content": "结果"} for tc in tool_calls]

        mock_openai_client = Mock()
        mock_openai_client.chat.completions.create = AsyncMock(
            side_effect=create_mock_response
        )
        client._client = mock_openai_client

        messages = [{"role": "user", "content": "test"}]
        tools = [{"type": "function", "function": {"name": "search_memory"}}]

        content, metadata = await client.chat_with_tools(
            messages=messages,
            tools=tools,
            tool_executor=mock_executor,
            max_tool_rounds=2,  # 限制为 2 轮
            timeout=60
        )

        assert "工具调用次数超过限制" in content
        assert metadata["tool_rounds"] == 2
