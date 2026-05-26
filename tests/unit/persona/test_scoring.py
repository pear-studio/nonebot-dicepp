"""
单元测试: ScoringAgent 工具路径（使用 AgentRuntime）
"""
import pytest
from unittest.mock import MagicMock, AsyncMock

from plugins.DicePP.module.persona.chat.scoring import ScoringAgent
from plugins.DicePP.module.persona.data.models import ScoreDeltas
from conftest import make_mock_runtime


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
    make_mock_runtime(monkeypatch)
    return ScoringAgent(mock_router, timezone="Asia/Shanghai", max_tool_rounds=3)


class TestScoringToolPath:
    """测试 ScoringAgent 工具路径"""

    @pytest.mark.asyncio
    async def test_normal_tool_call_collection(self, agent, mock_router):
        """正常工具调用收集 → _extract_result 解析"""
        mock_router._pending_tool_args = {"deltas": {"intimacy": 1.5, "passion": 0, "trust": 0.5, "secureness": 0}, "facts": {"爱好": "摄影"}}

        result = await agent.batch_analyze(
            messages=[
                {"role": "user", "content": "你好"},
                {"role": "assistant", "content": "你好！"},
            ],
        )

        assert result.deltas.intimacy == 1.5
        assert result.deltas.trust == 0.5
        assert result.facts == {"爱好": "摄影"}
        assert result.parse_error == ""

    @pytest.mark.asyncio
    async def test_empty_collected_fallback_to_parse_response(self, agent, mock_router):
        """collected 为空 → fallback 到 _parse_response(content)"""
        mock_router._pending_tool_args = None
        mock_router._pending_final_output = '{"deltas": {"intimacy": -1.0, "passion": 0, "trust": 0, "secureness": 0}, "facts": {}}'

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
