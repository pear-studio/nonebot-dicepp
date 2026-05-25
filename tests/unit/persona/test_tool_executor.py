"""ToolSpec/ToolRegistry/ToolExecutor 测试 — 注册、校验、执行、错误"""
import pytest
import json
from unittest.mock import Mock, AsyncMock
from pydantic import BaseModel, Field

from plugins.DicePP.module.persona.agent.tool_executor import ToolSpec, ToolRegistry, ToolExecutor
from plugins.DicePP.module.persona.agent.actions import EffectKind
from plugins.DicePP.module.persona.agent.event_bus import AgentEventBus, EventStore
from plugins.DicePP.module.persona.agent.state import AgentRunState


class SearchArgs(BaseModel):
    query: str = Field(description="search query")

class RollArgs(BaseModel):
    expr: str = Field(description="dice expression")


def _make_state(**kwargs) -> AgentRunState:
    defaults = dict(run_id="r1", turn_id="t1", user_id="u1", group_id="g1", mode="chat")
    defaults.update(kwargs)
    return AgentRunState(**defaults)


@pytest.fixture
def event_store():
    return Mock(spec=EventStore)


class TestToolRegistry:
    """ToolRegistry — 注册、查找、schema"""

    def test_register_and_get(self):
        reg = ToolRegistry()
        spec = ToolSpec(name="search", description="search tool", args_schema=SearchArgs, effect=EffectKind.PURE, executor=Mock())
        reg.register(spec)

        assert reg.get("search") is spec
        assert reg.get("unknown") is None

    def test_get_openai_schemas(self):
        reg = ToolRegistry()
        spec = ToolSpec(name="search", description="search tool", args_schema=SearchArgs, effect=EffectKind.PURE, executor=Mock())
        reg.register(spec)

        schemas = reg.get_openai_schemas()
        assert len(schemas) == 1
        assert schemas[0]["function"]["name"] == "search"
        assert schemas[0]["type"] == "function"
        assert "parameters" in schemas[0]["function"]

    def test_list_tools(self):
        reg = ToolRegistry()
        s1 = ToolSpec(name="a", description="a", args_schema=SearchArgs, effect=EffectKind.PURE, executor=Mock())
        s2 = ToolSpec(name="b", description="b", args_schema=RollArgs, effect=EffectKind.PURE, executor=Mock())
        reg.register(s1)
        reg.register(s2)

        tools = reg.list_tools()
        assert len(tools) == 2
        assert {t.name for t in tools} == {"a", "b"}

    def test_overwrite_existing(self):
        reg = ToolRegistry()
        s1 = ToolSpec(name="tool", description="v1", args_schema=SearchArgs, effect=EffectKind.PURE, executor=Mock())
        s2 = ToolSpec(name="tool", description="v2", args_schema=RollArgs, effect=EffectKind.PURE, executor=Mock())
        reg.register(s1)
        reg.register(s2)

        assert reg.get("tool").description == "v2"


class TestToolExecutor:
    """ToolExecutor — 参数校验、执行、事件、错误"""

    @pytest.fixture
    def registry(self):
        reg = ToolRegistry()
        search_exec = AsyncMock(return_value="search result")
        reg.register(ToolSpec(name="search", description="search", args_schema=SearchArgs, effect=EffectKind.PURE, executor=search_exec))
        roll_exec = AsyncMock(return_value="roll result")
        reg.register(ToolSpec(name="roll", description="roll", args_schema=RollArgs, effect=EffectKind.PURE, executor=roll_exec))
        return reg

    @pytest.fixture
    def executor(self, registry, event_store):
        bus = AgentEventBus(event_store=event_store)
        return ToolExecutor(registry=registry, event_bus=bus)

    @pytest.mark.asyncio
    async def test_execute_success(self, executor, registry):
        state = _make_state()
        tc = {"id": "tc_1", "name": "search", "arguments": json.dumps({"query": "test"})}

        results = await executor.execute_many([tc], state)

        assert len(results) == 1
        assert results[0]["tool_call_id"] == "tc_1"
        assert results[0]["content"] == "search result"

    @pytest.mark.asyncio
    async def test_execute_unknown_tool(self, executor, registry):
        state = _make_state()
        tc = {"id": "tc_1", "name": "unknown_tool", "arguments": "{}"}

        results = await executor.execute_many([tc], state)

        assert "未注册" in results[0]["content"]

    @pytest.mark.asyncio
    async def test_execute_invalid_json(self, executor, registry):
        state = _make_state()
        tc = {"id": "tc_1", "name": "search", "arguments": "not-json"}

        results = await executor.execute_many([tc], state)

        assert "解析失败" in results[0]["content"]

    @pytest.mark.asyncio
    async def test_execute_validation_error(self, executor, registry):
        """缺少必需的 query 字段"""
        state = _make_state()
        tc = {"id": "tc_1", "name": "search", "arguments": json.dumps({"wrong_field": "x"})}

        results = await executor.execute_many([tc], state)

        assert "校验失败" in results[0]["content"]

    @pytest.mark.asyncio
    async def test_execute_multiple(self, executor, registry):
        state = _make_state()
        tcs = [
            {"id": "tc_1", "name": "search", "arguments": json.dumps({"query": "a"})},
            {"id": "tc_2", "name": "roll", "arguments": json.dumps({"expr": "1d6"})},
        ]

        results = await executor.execute_many(tcs, state)

        assert len(results) == 2
        assert results[0]["tool_call_id"] == "tc_1"
        assert results[1]["tool_call_id"] == "tc_2"

    @pytest.mark.asyncio
    async def test_executor_exception_caught(self, executor, registry):
        """工具执行中抛异常 → 返回错误结果"""
        # 替换 search executor 为抛异常的
        failing_exec = AsyncMock(side_effect=RuntimeError("executor crash"))
        registry.register(ToolSpec(name="search", description="search", args_schema=SearchArgs, effect=EffectKind.PURE, executor=failing_exec))

        state = _make_state()
        tc = {"id": "tc_1", "name": "search", "arguments": json.dumps({"query": "x"})}

        results = await executor.execute_many([tc], state)

        assert "执行失败" in results[0]["content"]
        assert "executor crash" in results[0]["content"]

    @pytest.mark.asyncio
    async def test_external_action_has_action_id(self, executor, registry, event_store):
        """EXTERNAL_ACTION 工具返回含 _action_id 的结果"""
        send_exec = AsyncMock(return_value='{"phase": "final", "content": "hello"}')
        registry.register(ToolSpec(name="send_reply_segment", description="send", args_schema=RollArgs, effect=EffectKind.EXTERNAL_ACTION, executor=send_exec))

        state = _make_state()
        tc = {"id": "tc_1", "name": "send_reply_segment", "arguments": json.dumps({"expr": "1d6"})}

        results = await executor.execute_many([tc], state)

        assert "_action_id" in results[0]
        assert results[0]["_action_id"] != ""
        # EXTERNAL_ACTION 的内容就是 executor 返回值
        assert '{"phase": "final", "content": "hello"}' in results[0]["content"]


class TestToolSpec:
    """ToolSpec 数据类"""

    def test_construction(self):
        spec = ToolSpec(name="test", description="test tool", args_schema=SearchArgs, effect=EffectKind.PURE, executor=Mock())
        assert spec.name == "test"
        assert spec.args_schema is SearchArgs
        assert spec.effect == EffectKind.PURE

    def test_all_effect_kinds(self):
        for kind in EffectKind:
            spec = ToolSpec(name=kind.value, description="", args_schema=SearchArgs, effect=kind, executor=Mock())
            assert spec.effect == kind
