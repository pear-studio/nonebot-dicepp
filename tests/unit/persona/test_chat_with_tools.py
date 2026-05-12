"""
Phase 3: 工具调用集成测试

测试 _chat_with_tools 完整流程
"""

import pytest
import asyncio
from typing import List, Dict, Any
from unittest.mock import Mock, AsyncMock

from plugins.DicePP.module.persona.llm.client import LLMClient, ForcedToolError, RoundResult
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

        rr = metadata["round_records"]
        assert len(rr) == 1
        assert rr[0]["round"] == 0
        assert rr[0]["think"] is None
        assert rr[0]["tool_calls"] == []
        assert rr[0]["tool_results"] == []
        assert rr[0]["callback"] is None

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

        # round_records: 2 entries — tool call round + final round
        rr = metadata["round_records"]
        assert len(rr) == 2
        assert rr[0]["round"] == 0
        assert rr[0]["think"] is None
        assert rr[0]["callback"] is None
        assert rr[0]["tool_calls"] == [{"id": "tc_1", "name": "search_memory", "arguments": '{"query": "猫"}'}]
        assert rr[0]["tool_results"] == [{"tool_call_id": "tc_1", "content": "工具 search_memory 执行结果"}]
        assert rr[1]["round"] == 1  # tool_round_num 在上轮递增
        assert rr[1]["think"] is None
        assert rr[1]["tool_calls"] == []
        assert rr[1]["tool_results"] == []

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

        rr = metadata["round_records"]
        assert len(rr) == 2
        assert rr[0]["round"] == 0
        assert rr[0]["tool_calls"][0]["name"] == "search_memory"
        assert len(rr[0]["tool_results"]) == 1
        assert "temporarily unavailable" in rr[0]["tool_results"][0]["content"]
        assert rr[0]["tool_results"][0]["tool_call_id"] == "tc_1"

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

        rr = metadata["round_records"]
        assert len(rr) == 1
        assert rr[0]["round"] == 0
        assert rr[0]["tool_calls"][0]["name"] == "search_memory"
        assert rr[0]["tool_results"] == []  # 执行失败，无结果

    @pytest.mark.asyncio
    async def test_generate_with_forced_tool_no_tool_calls_raises(self):
        """强制工具调用时 LLM 未返回 tool_calls，应抛出 ForcedToolError"""
        client = LLMClient(
            api_key="test_key",
            base_url="https://api.test.com/v1",
            model="gpt-4o"
        )

        mock_response = Mock()
        mock_response.choices = [Mock()]
        mock_response.choices[0].message = Mock()
        mock_response.choices[0].message.content = "我没有调用工具"
        mock_response.choices[0].message.tool_calls = None
        mock_response.usage = Mock()
        mock_response.usage.prompt_tokens = 50
        mock_response.usage.completion_tokens = 10

        mock_openai_client = Mock()
        mock_openai_client.chat.completions.create = AsyncMock(return_value=mock_response)
        client._client = mock_openai_client

        with pytest.raises(ForcedToolError) as exc_info:
            await client.generate_with_forced_tool(
                messages=[{"role": "user", "content": "test"}],
                tools=[{"type": "function", "function": {"name": "test_tool"}}],
                tool_name="test_tool",
            )

        assert "test_tool" in str(exc_info.value)
        assert exc_info.value.raw_content == "我没有调用工具"

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
            max_round_callbacks=0,  # 禁用回调以保持原测试语义
            timeout=60
        )

        assert "工具调用次数超过限制" in content
        assert metadata["tool_rounds"] == 2

        rr = metadata["round_records"]
        assert len(rr) == 2
        assert rr[0]["round"] == 0
        assert rr[0]["tool_calls"][0]["name"] == "search_memory"
        assert len(rr[0]["tool_results"]) == 1
        assert rr[1]["round"] == 1
        assert rr[1]["tool_calls"][0]["name"] == "search_memory"
        assert len(rr[1]["tool_results"]) == 1

    @pytest.mark.asyncio
    async def test_callback_injection_appends_message_and_continues(self):
        """回调返回 dict 时注入消息并继续循环"""
        client = LLMClient(
            api_key="test_key",
            base_url="https://api.test.com/v1",
            model="gpt-4o"
        )

        # Round 0: content without tool_calls -> callback injects system note
        mock_response1 = Mock()
        mock_response1.choices = [Mock()]
        mock_response1.choices[0].message = Mock()
        mock_response1.choices[0].message.content = "hello"
        mock_response1.choices[0].message.tool_calls = None
        mock_response1.usage = None

        # Round 1 (after injection): content again -> callback returns None
        mock_response2 = Mock()
        mock_response2.choices = [Mock()]
        mock_response2.choices[0].message = Mock()
        mock_response2.choices[0].message.content = "world"
        mock_response2.choices[0].message.tool_calls = None
        mock_response2.usage = None

        mock_openai_client = Mock()
        mock_openai_client.chat.completions.create = AsyncMock(
            side_effect=[mock_response1, mock_response2]
        )
        client._client = mock_openai_client

        async def on_round(round_num, result, messages):
            if result.content == "hello":
                return {"role": "system", "content": "inject"}
            return None

        content, metadata = await client.chat_with_tools(
            messages=[{"role": "user", "content": "test"}],
            tools=[],
            on_round_complete=on_round,
            max_round_callbacks=3,
            max_tool_rounds=5,
        )

        assert content == "world"
        assert metadata["callback_count"] == 1
        assert metadata["tool_rounds"] == 0

        rr = metadata["round_records"]
        assert len(rr) == 2
        assert rr[0]["callback"] == {"role": "system", "content": "inject"}
        assert rr[0]["tool_calls"] == []
        assert rr[0]["tool_results"] == []
        assert rr[1]["callback"] is None

        # Verify injected message was appended
        second_call_messages = mock_openai_client.chat.completions.create.await_args_list[1][1]["messages"]
        assert any(m.get("content") == "inject" for m in second_call_messages)

    @pytest.mark.asyncio
    async def test_callback_returns_none_uses_original_flow(self):
        """回调返回 None 时走原流程（处理 tool_calls）"""
        client = LLMClient(
            api_key="test_key",
            base_url="https://api.test.com/v1",
            model="gpt-4o"
        )

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

        async def mock_executor(tool_calls):
            return [{"tool_call_id": tc["id"], "content": "ok"} for tc in tool_calls]

        async def on_round(round_num, result, messages):
            return None

        content, metadata = await client.chat_with_tools(
            messages=[{"role": "user", "content": "test"}],
            tools=[{"type": "function", "function": {"name": "search_memory"}}],
            tool_executor=mock_executor,
            on_round_complete=on_round,
            max_round_callbacks=2,
            max_tool_rounds=1,
        )

        assert metadata["callback_count"] == 0  # on_round 返回 None，不递增
        assert metadata["total_tool_calls"] > 0   # 确实走了 tool_calls 处理流程

    @pytest.mark.asyncio
    async def test_max_round_callbacks_stops_injection(self):
        """callback_count 达到 max_round_callbacks 后不再注入"""
        client = LLMClient(
            api_key="test_key",
            base_url="https://api.test.com/v1",
            model="gpt-4o"
        )

        mock_openai_client = Mock()
        mock_openai_client.chat.completions.create = AsyncMock(
            side_effect=[
                self._make_response("a"),
                self._make_response("b"),
                self._make_response("c"),
            ]
        )
        client._client = mock_openai_client

        async def on_round(round_num, result, messages):
            return {"role": "system", "content": "inject"}

        content, metadata = await client.chat_with_tools(
            messages=[{"role": "user", "content": "test"}],
            tools=[],
            on_round_complete=on_round,
            max_round_callbacks=2,
            max_tool_rounds=2,
        )

        assert metadata["callback_count"] == 2
        # max_total_rounds = 2 + 2 = 4, but after 2 injections total_rounds=3
        # then round 3 (total_rounds=4) returns "c" without injection (callback exhausted)

    def _make_response(self, content: str):
        mock_response = Mock()
        mock_response.choices = [Mock()]
        mock_response.choices[0].message = Mock()
        mock_response.choices[0].message.content = content
        mock_response.choices[0].message.tool_calls = None
        mock_response.usage = None
        return mock_response

    @pytest.mark.asyncio
    async def test_max_total_rounds_enforced(self):
        """total_rounds 达到 max_total_rounds 时强制退出"""
        client = LLMClient(
            api_key="test_key",
            base_url="https://api.test.com/v1",
            model="gpt-4o"
        )

        mock_openai_client = Mock()
        mock_openai_client.chat.completions.create = AsyncMock(
            side_effect=[
                self._make_response("a"),
                self._make_response("b"),
            ]
        )
        client._client = mock_openai_client

        async def on_round(round_num, result, messages):
            return {"role": "system", "content": "inject"}

        content, metadata = await client.chat_with_tools(
            messages=[{"role": "user", "content": "test"}],
            tools=[],
            on_round_complete=on_round,
            max_round_callbacks=1,
            max_tool_rounds=1,
        )

        # max_total_rounds = 1 + 1 = 2
        # round 0: "a" -> inject (callback_count=1, total_rounds=1)
        # round 1: "b" -> callback exhausted, return "b" (total_rounds=2)
        assert content == "b"
        assert metadata["callback_count"] == 1

    @pytest.mark.asyncio
    async def test_generate_with_forced_tool_does_not_accept_callback(self):
        """generate_with_forced_tool 签名不包含 callback 参数"""
        import inspect
        sig = inspect.signature(LLMClient.generate_with_forced_tool)
        assert "on_round_complete" not in sig.parameters
        assert "max_round_callbacks" not in sig.parameters

    @pytest.mark.asyncio
    async def test_round_records_with_think_and_multiple_tool_rounds(self):
        """round_records 完整记录 think / tool_calls / tool_results / callback"""
        client = LLMClient(
            api_key="test_key",
            base_url="https://api.test.com/v1",
            model="gpt-4o"
        )

        # Round 0: think + tool_call
        r0 = Mock()
        r0.choices = [Mock()]
        r0.choices[0].message = Mock()
        r0.choices[0].message.content = "<think>需要查询记忆</think>"
        r0.choices[0].message.tool_calls = [
            MockToolCall("tc_1", "search_memory", '{"query":"猫"}')
        ]
        r0.usage = None

        # Round 1: think + tool_call (second round, different tool)
        r1 = Mock()
        r1.choices = [Mock()]
        r1.choices[0].message = Mock()
        r1.choices[0].message.content = "<think>还需要掷骰</think>"
        r1.choices[0].message.tool_calls = [
            MockToolCall("tc_2", "roll_dice", '{"expr":"2d6"}')
        ]
        r1.usage = None

        # Round 2: think + final response (no tool_calls)
        r2 = Mock()
        r2.choices = [Mock()]
        r2.choices[0].message = Mock()
        r2.choices[0].message.content = "<think>回答准备好了</think>你喜欢猫！"
        r2.choices[0].message.tool_calls = None
        r2.usage = None

        mock_openai_client = Mock()
        mock_openai_client.chat.completions.create = AsyncMock(
            side_effect=[r0, r1, r2]
        )
        client._client = mock_openai_client

        async def mock_executor(tool_calls):
            return [
                {"tool_call_id": tc["id"], "content": f"[{tc['name']}] result"}
                for tc in tool_calls
            ]

        content, metadata = await client.chat_with_tools(
            messages=[{"role": "user", "content": "test"}],
            tools=[
                {"type": "function", "function": {"name": "search_memory"}},
                {"type": "function", "function": {"name": "roll_dice"}},
            ],
            tool_executor=mock_executor,
            max_tool_rounds=5,
        )

        assert content == "你喜欢猫！"
        assert metadata["tool_rounds"] == 2
        assert metadata["total_tool_calls"] == 2

        rr = metadata["round_records"]
        assert len(rr) == 3  # 2 tool rounds + 1 final

        # Round 0: tool call
        assert rr[0]["round"] == 0
        assert rr[0]["think"] == "<think>需要查询记忆</think>"
        assert rr[0]["callback"] is None
        assert rr[0]["tool_calls"] == [
            {"id": "tc_1", "name": "search_memory", "arguments": '{"query":"猫"}'}
        ]
        assert rr[0]["tool_results"] == [
            {"tool_call_id": "tc_1", "content": "[search_memory] result"}
        ]

        # Round 1: second tool call
        assert rr[1]["round"] == 1
        assert rr[1]["think"] == "<think>还需要掷骰</think>"
        assert rr[1]["tool_calls"] == [
            {"id": "tc_2", "name": "roll_dice", "arguments": '{"expr":"2d6"}'}
        ]
        assert rr[1]["tool_results"] == [
            {"tool_call_id": "tc_2", "content": "[roll_dice] result"}
        ]

        # Round 2: final, no tool_calls, think preserved
        assert rr[2]["round"] == 2
        assert rr[2]["think"] == "<think>回答准备好了</think>"
        assert rr[2]["tool_calls"] == []
        assert rr[2]["tool_results"] == []


class TestFilterThinkTags:
    """<think> 标签过滤（client 层模型输出归一化）"""

    def test_filters_single_think_block(self):
        client = LLMClient(api_key="k", base_url="http://x", model="m")
        result = client._filter_think_tags("<think>用户问的是前两个观察</think>")
        assert result == ""

    def test_filters_multiple_think_blocks(self):
        client = LLMClient(api_key="k", base_url="http://x", model="m")
        result = client._filter_think_tags(
            "<think>分析1</think>\n<think>分析2</think>"
        )
        assert result == ""

    def test_preserves_text_after_think(self):
        client = LLMClient(api_key="k", base_url="http://x", model="m")
        result = client._filter_think_tags(
            "<think>需要回答</think>你好我是苏晓"
        )
        assert result == "你好我是苏晓"

    def test_preserves_text_before_think(self):
        client = LLMClient(api_key="k", base_url="http://x", model="m")
        result = client._filter_think_tags(
            "你好<think>这里是思考</think>再见"
        )
        assert result == "你好再见"

    def test_handles_multiline_think(self):
        client = LLMClient(api_key="k", base_url="http://x", model="m")
        result = client._filter_think_tags(
            "<think>\n分析中...\n需要搜索对话历史\n</think>\n"
        )
        assert result == ""

    def test_no_think_tags_unchanged(self):
        client = LLMClient(api_key="k", base_url="http://x", model="m")
        result = client._filter_think_tags("这是普通文本")
        assert result == "这是普通文本"

    def test_empty_string_unchanged(self):
        client = LLMClient(api_key="k", base_url="http://x", model="m")
        result = client._filter_think_tags("")
        assert result == ""

    def test_whitespace_only_stripped(self):
        client = LLMClient(api_key="k", base_url="http://x", model="m")
        result = client._filter_think_tags("  \t\n  ")
        assert result == ""


class TestExtractThink:
    """_extract_think 功能验证"""

    def test_extracts_single_think_block(self):
        client = LLMClient(api_key="k", base_url="http://x", model="m")
        think = client._extract_think("<think>用户问的是前两个观察</think>")
        assert think == "<think>用户问的是前两个观察</think>"

    def test_extracts_multiple_think_blocks(self):
        client = LLMClient(api_key="k", base_url="http://x", model="m")
        think = client._extract_think("<think>A</think>\n<think>B</think>")
        assert think == "<think>A</think><think>B</think>"

    def test_returns_none_when_no_think(self):
        client = LLMClient(api_key="k", base_url="http://x", model="m")
        think = client._extract_think("这是普通文本")
        assert think is None

    def test_handles_multiline_think(self):
        client = LLMClient(api_key="k", base_url="http://x", model="m")
        think = client._extract_think("<think>\n分析中...\n</think>")
        assert think == "<think>\n分析中...\n</think>"

    def test_only_extracts_think_blocks_not_surrounding_text(self):
        client = LLMClient(api_key="k", base_url="http://x", model="m")
        think = client._extract_think("你好<think>思考</think>再见")
        assert think == "<think>思考</think>"
