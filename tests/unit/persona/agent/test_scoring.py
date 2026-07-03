"""
单元测试: ScoringAgent 工具路径（使用 AgentRuntime）
"""
import pytest
from unittest.mock import MagicMock, AsyncMock

from plugins.DicePP.module.persona.chat.scoring import ScoringAgent
from plugins.DicePP.module.persona.data.models import ScoreDeltas
from plugins.DicePP.module.persona.agent.runtime import AgentRuntime
from conftest import make_fake_runtime_run


@pytest.fixture
def mock_router():
    router = MagicMock()
    router.data_store = MagicMock()
    router.quota_check_enabled = False
    router.daily_limit = 20
    router.trace_enabled = False
    router.trace_max_age_days = 7
    router.config = MagicMock()
    router.config.timezone = "Asia/Shanghai"
    router._pending_tool_args = None
    router._pending_final_output = "ok"
    return router


@pytest.fixture
def agent(mock_router, monkeypatch):
    monkeypatch.setattr(AgentRuntime, "run", make_fake_runtime_run())
    return ScoringAgent(mock_router, timezone="Asia/Shanghai", max_rounds=3)


class TestScoringToolPath:
    """测试 ScoringAgent 工具路径"""

    @pytest.mark.asyncio
    async def test_normal_tool_call_collection(self, agent, mock_router):
        """正常工具调用收集 → _extract_result 解析"""
        mock_router._pending_tool_args = {"deltas": {"intimacy": 1.5, "reputation_delta": -5.0, "warning_issued": True}, "facts": {"爱好": "摄影"}}

        result = await agent.batch_analyze(
            messages=[
                {"role": "user", "content": "你好"},
                {"role": "assistant", "content": "你好！"},
            ],
        )

        assert result.deltas.intimacy == 1.5
        assert result.deltas.reputation_delta == -5.0
        assert result.deltas.warning_issued is True
        assert result.facts == {"爱好": "摄影"}
        assert result.parse_error == ""

    @pytest.mark.asyncio
    async def test_empty_collected_fallback_to_parse_response(self, agent, mock_router):
        """collected 为空 → fallback 到 _parse_response(content)"""
        mock_router._pending_tool_args = None
        mock_router._pending_final_output = '{"deltas": {"intimacy": -1.0, "reputation_delta": 0.0}, "facts": {}}'

        result = await agent.batch_analyze(
            messages=[{"role": "user", "content": "test"}, {"role": "assistant", "content": "ok"}],
        )

        assert result.deltas.intimacy == -1.0
        assert result.parse_error == ""

    @pytest.mark.asyncio
    async def test_llm_call_failure(self, agent, mock_router, monkeypatch):
        """LLM 调用异常 → 返回 parse_error"""
        from plugins.DicePP.module.persona.agent.runtime import AgentRuntime

        async def failing_run(self, messages, user_id, group_id, tool_registry, **kwargs):
            raise Exception("服务不可用")

        monkeypatch.setattr(AgentRuntime, "run", failing_run)
        result = await agent.batch_analyze(
            messages=[{"role": "user", "content": "test"}, {"role": "assistant", "content": "ok"}],
        )

        assert "LLM 调用失败" in result.parse_error
        assert result.deltas == ScoreDeltas()
        assert result.facts == {}

    @pytest.mark.asyncio
    async def test_empty_collected_and_empty_content(self, agent, mock_router):
        """collected 为空且 content 为空 → fallback 返回空结果"""
        mock_router._pending_tool_args = None
        mock_router._pending_final_output = ""

        result = await agent.batch_analyze(
            messages=[{"role": "user", "content": "test"}, {"role": "assistant", "content": "ok"}],
        )

        assert result.deltas == ScoreDeltas()
        assert result.parse_error != ""


class TestScoringLLMAbnormal:
    """R1: 验证 batch_analyze 在 ToolLoop 返回异常 final_reason 时显式标记错误"""

    @pytest.mark.asyncio
    async def test_scoring_llm_abnormal_reason(self, agent, mock_router, monkeypatch):
        """LLM 返回异常 final_reason 时应显式标记 parse_error"""
        from plugins.DicePP.module.persona.agent.runtime import AgentRuntime
        from plugins.DicePP.module.persona.agent.loop import AgentRunResult

        async def abnormal_run(self, messages, user_id, group_id, tool_registry, **kwargs):
            return AgentRunResult(
                run_id="test",
                turn_id="test",
                status="completed",
                final_reason="provider_error",
                final_text="",
                final_messages=list(messages),
                delivery_performed=False,
            )

        monkeypatch.setattr(AgentRuntime, "run", abnormal_run)
        result = await agent.batch_analyze(
            messages=[{"role": "user", "content": "test"}, {"role": "assistant", "content": "ok"}],
        )

        assert "LLM 协议错误" in result.parse_error, (
            f"期望 parse_error 含 'LLM 协议错误' 但得到 '{result.parse_error}' —— "
            f"final_reason='provider_error' 时应返回精确错误信息而非默认 total_score=50"
        )


class TestScoringLLMAbnormalMultipleReasons:
    """Q24: LLM abnormal error paths — 验证各种异常 final_reason 产生正确的 parse_error"""

    @pytest.fixture
    def mock_router_q24(self):
        router = MagicMock()
        router.data_store = MagicMock()
        router.quota_check_enabled = False
        router.daily_limit = 20
        router.trace_enabled = False
        router.trace_max_age_days = 7
        router.config = MagicMock()
        router.config.timezone = "Asia/Shanghai"
        router._pending_tool_args = None
        router._pending_final_output = ""
        return router

    @pytest.mark.asyncio
    @pytest.mark.parametrize("abnormal_reason", [
        "provider_error",
        "timeout",
        "content_filter",
        "tool_corrections_exhausted",
        "interim_corrections_exhausted",
        "required_tool_missing",
        "llm_error",
    ])
    async def test_abnormal_reason_returns_protocol_error(self, mock_router_q24, monkeypatch, abnormal_reason):
        """各种异常 final_reason 均返回 'LLM 协议错误'"""
        from plugins.DicePP.module.persona.chat.scoring import ScoringAgent
        from plugins.DicePP.module.persona.agent.loop import AgentRunResult
        from plugins.DicePP.module.persona.agent.runtime import AgentRuntime

        async def abnormal_run(self, messages, user_id, group_id, tool_registry, **kwargs):
            return AgentRunResult(
                run_id="test", turn_id="test",
                status="completed" if abnormal_reason != "llm_error" else "failed",
                final_reason=abnormal_reason,
                final_text="",
                final_messages=list(messages),
                delivery_performed=False,
            )

        monkeypatch.setattr(AgentRuntime, "run", abnormal_run)

        agent = ScoringAgent(mock_router_q24, timezone="Asia/Shanghai", max_rounds=3)
        result = await agent.batch_analyze(
            messages=[{"role": "user", "content": "hi"}, {"role": "assistant", "content": "ok"}],
        )

        assert result.parse_error == "LLM 协议错误", (
            f"final_reason={abnormal_reason!r} 应返回 'LLM 协议错误', "
            f"实际得到 {result.parse_error!r}"
        )
        assert result.deltas.intimacy == 0.0
        assert result.facts == {}

    @pytest.mark.asyncio
    @pytest.mark.parametrize("normal_reason", ["stop", "max_rounds", "direct_content"])
    async def test_normal_reason_does_not_return_protocol_error(self, mock_router_q24, monkeypatch, normal_reason):
        """正常 final_reason 不返回 'LLM 协议错误'（走后续解析）"""
        from plugins.DicePP.module.persona.chat.scoring import ScoringAgent
        from plugins.DicePP.module.persona.agent.loop import AgentRunResult
        from plugins.DicePP.module.persona.agent.runtime import AgentRuntime

        async def normal_run(self, messages, user_id, group_id, tool_registry, **kwargs):
            return AgentRunResult(
                run_id="test", turn_id="test",
                status="completed",
                final_reason=normal_reason,
                final_text='{"deltas": {"intimacy": 1.0, "reputation_delta": 0.0}, "facts": {}}',
                final_messages=list(messages),
                delivery_performed=False,
            )

        monkeypatch.setattr(AgentRuntime, "run", normal_run)

        agent = ScoringAgent(mock_router_q24, timezone="Asia/Shanghai", max_rounds=3)
        result = await agent.batch_analyze(
            messages=[{"role": "user", "content": "hi"}, {"role": "assistant", "content": "ok"}],
        )

        # 正常 reason 不会返回协议错误，应走解析路径
        assert result.parse_error != "LLM 协议错误", (
            f"final_reason={normal_reason!r} 不应返回 'LLM 协议错误'"
        )


class TestToolLoopToolCollection:
    """Q26: tool path tool collection — 验证 ToolLoop 正常收集 RECORD_EVENT/RECORD_REACTION 等工具调用结果"""

    def test_extract_tool_args_record_event(self):
        """_extract_tool_args 提取 RECORD_EVENT 工具调用参数"""
        from plugins.DicePP.module.persona.life.tool_loop import _extract_tool_args

        new_messages = [
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "name": "record_event", "input": {"event_type": "birthday", "description": "用户生日"}},
                ],
            },
        ]
        collected = _extract_tool_args(new_messages, {"record_event"})
        assert len(collected) == 1
        name, args = collected[0]
        assert name == "record_event"
        assert args == {"event_type": "birthday", "description": "用户生日"}

    def test_extract_tool_args_record_reaction(self):
        """_extract_tool_args 提取 RECORD_REACTION 工具调用参数"""
        from plugins.DicePP.module.persona.life.tool_loop import _extract_tool_args

        new_messages = [
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "function": {
                            "name": "record_reaction",
                            "arguments": '{"reaction": "agree", "target": "proposal_1"}',
                        },
                    },
                ],
            },
        ]
        collected = _extract_tool_args(new_messages, {"record_reaction"})
        assert len(collected) == 1
        name, args = collected[0]
        assert name == "record_reaction"
        assert args == {"reaction": "agree", "target": "proposal_1"}

    def test_extract_tool_args_multiple_tools(self):
        """_extract_tool_args 同时提取多个工具调用"""
        from plugins.DicePP.module.persona.life.tool_loop import _extract_tool_args

        new_messages = [
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "name": "record_event", "input": {"event_type": "meeting"}},
                    {"type": "tool_use", "name": "record_reaction", "input": {"reaction": "like"}},
                ],
            },
        ]
        collected = _extract_tool_args(new_messages, {"record_event", "record_reaction"})
        assert len(collected) == 2
        names = {n for n, _ in collected}
        assert names == {"record_event", "record_reaction"}

    @pytest.mark.asyncio
    async def test_parse_tool_args_via_tool_loop(self, monkeypatch):
        """验证 ToolLoop.execute 在 collect 模式下收集工具调用参数"""
        from plugins.DicePP.module.persona.life.tool_loop import ToolLoop, ToolResult
        from plugins.DicePP.module.persona.life.conversation import RunConfig
        from plugins.DicePP.module.persona.agent.loop import AgentRunResult
        from plugins.DicePP.module.persona.agent.runtime import AgentRuntime
        from plugins.DicePP.module.persona.agent.request import AgentRunLimits
        from plugins.DicePP.module.persona.life.tool_loop import _parse_tool_args

        router = MagicMock()
        router.data_store = MagicMock()
        store = MagicMock()

        # 构建 AgentRunResult 带工具调用结果
        final_messages = [
            {"role": "user", "content": "analyze"},
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "name": "record_event", "input": {"event_type": "achievement", "description": "完成任务"}},
                    {"type": "tool_use", "name": "record_reaction", "input": {"reaction": "excited"}},
                ],
            },
        ]

        async def fake_run(self, messages, user_id, group_id, tool_registry, **kwargs):
            return AgentRunResult(
                run_id="test", turn_id="test", status="completed",
                final_reason="stop", final_text="",
                final_messages=final_messages,
                delivery_performed=False,
            )

        monkeypatch.setattr(AgentRuntime, "run", fake_run)

        tool_loop = ToolLoop(router=router, store=store, limits=AgentRunLimits(max_rounds=3))
        result = await tool_loop.execute(
            messages=[{"role": "user", "content": "analyze"}],
            config=RunConfig(
                mode="collect",
                required_tools=["record_event", "record_reaction"],
                tools=None,
                temperature=0.7,
                timeout=60,
                selection=None,
            ),
        )

        # 验证 new_messages 包含增量消息
        assert len(result.new_messages) == 1
        assert result.new_messages[0]["role"] == "assistant"

        # 通过 _parse_tool_args 提取参数
        event_args = _parse_tool_args(result.new_messages, "record_event")
        assert len(event_args) == 1
        assert event_args[0]["event_type"] == "achievement"

        reaction_args = _parse_tool_args(result.new_messages, "record_reaction")
        assert len(reaction_args) == 1
        assert reaction_args[0]["reaction"] == "excited"
