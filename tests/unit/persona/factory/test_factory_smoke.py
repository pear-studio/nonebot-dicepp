"""Factory 模块冒烟测试 — 验证关键组件可导入且工厂类型正确（T6 新路径）"""
import pytest


class TestFactoryImports:
    """验证 factory 模块的关键导出可正常导入"""

    def test_persona_app_import(self):
        from plugins.DicePP.module.persona.factory import PersonaApp
        assert PersonaApp is not None

    def test_chat_orchestrator_import(self):
        from plugins.DicePP.module.persona.chat.orchestrator import ChatOrchestrator
        assert ChatOrchestrator is not None

    def test_life_simulator_import(self):
        from plugins.DicePP.module.persona.life.simulator import LifeSimulator
        assert LifeSimulator is not None


class TestRuntimeTypes:
    """验证 T6 核心 Runtime 类型正确"""

    def test_run_metadata_fields(self):
        from plugins.DicePP.module.persona.agent.runtime_types import RunMetadata
        meta = RunMetadata(agent_name="test", run_tag="chat", user_id="u1", group_id="g1")
        assert meta.agent_name == "test"
        assert meta.run_tag == "chat"
        assert meta.user_id == "u1"
        assert meta.group_id == "g1"

    def test_run_metadata_defaults(self):
        from plugins.DicePP.module.persona.agent.runtime_types import RunMetadata
        meta = RunMetadata()
        assert meta.user_id == ""
        assert meta.group_id == ""

    def test_tool_kit_empty(self):
        from plugins.DicePP.module.persona.agent.runtime_types import ToolKit
        tk = ToolKit()
        assert len(tk.tools) == 0
        assert tk.get_openai_schemas() == []

    def test_output_spec_creation(self):
        from plugins.DicePP.module.persona.agent.runtime_types import OutputSpec, SendReplyArgs
        spec = OutputSpec(
            name="send_reply",
            description="发送回复",
            args_schema=SendReplyArgs,
        )
        assert spec.name == "send_reply"
        assert spec.args_schema is SendReplyArgs

    def test_output_spec_empty_name_raises(self):
        """R6: OutputSpec(name='') 抛出 ValueError"""
        from plugins.DicePP.module.persona.agent.runtime_types import OutputSpec, SendReplyArgs
        import pytest
        with pytest.raises(ValueError, match="name 不能为空"):
            OutputSpec(name="", description="test", args_schema=SendReplyArgs)

    def test_output_spec_whitespace_name_raises(self):
        """R6: OutputSpec(name='   ') 抛出 ValueError"""
        from plugins.DicePP.module.persona.agent.runtime_types import OutputSpec, SendReplyArgs
        import pytest
        with pytest.raises(ValueError, match="name 不能为空"):
            OutputSpec(name="   ", description="test", args_schema=SendReplyArgs)

    def test_run_completion_enum_values(self):
        from plugins.DicePP.module.persona.agent.runtime_types import RunCompletion
        completed = RunCompletion(kind="completed", code="ok")
        assert completed.kind == "completed"
        failed = RunCompletion(kind="failed", code="error", message="fail")
        assert failed.kind == "failed"
        assert failed.message == "fail"
        limit = RunCompletion(kind="limit_reached", code="max_rounds")
        assert limit.kind == "limit_reached"


class TestAgentRuntime:
    """AgentRuntime 构造测试"""

    def test_constructor_minimal(self):
        from plugins.DicePP.module.persona.agent.runtime import AgentRuntime
        from plugins.DicePP.module.persona.agent.runtime_types import LoopLimits

        client = object()
        store = object()
        runtime = AgentRuntime(client=client, store=store)
        assert runtime._client is client
        assert runtime._store is store
        assert isinstance(runtime._limits, LoopLimits)

    def test_constructor_with_limits(self):
        from unittest.mock import Mock
        from plugins.DicePP.module.persona.agent.runtime import AgentRuntime
        from plugins.DicePP.module.persona.agent.runtime_types import LoopLimits

        limits = LoopLimits(max_rounds=5)
        runtime = AgentRuntime(client=Mock(), store=Mock(), limits=limits)
        assert runtime._limits is limits


class TestToolKitBuilder:
    """ToolKit + ToolSpec 构造和 OpenAI schema 生成"""

    def test_get_openai_schemas_single_tool(self):
        from plugins.DicePP.module.persona.agent.runtime_types import ToolKit, ToolSpec
        from pydantic import BaseModel, Field

        class TestArgs(BaseModel):
            query: str = Field(..., description="搜索关键词")

        async def handler(args, ctx):
            from plugins.DicePP.module.persona.agent.runtime_types import ToolResult
            return ToolResult(observation=f"found: {args.query}")

        spec = ToolSpec(name="search", description="搜索", args_schema=TestArgs, handler=handler)
        tk = ToolKit(tools={"search": spec})
        schemas = tk.get_openai_schemas()
        assert len(schemas) == 1
        assert schemas[0]["type"] == "function"
        assert schemas[0]["function"]["name"] == "search"
