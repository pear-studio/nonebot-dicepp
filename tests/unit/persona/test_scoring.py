"""
单元测试: ScoringAgent 工具路径（使用 router.run_via_loop）
"""
import pytest
from unittest.mock import MagicMock, AsyncMock

from plugins.DicePP.module.persona.chat.scoring import ScoringAgent
from plugins.DicePP.module.persona.data.models import ScoreDeltas
from plugins.DicePP.module.persona.llm.providers.protocol import LLMResponse, TokenUsage, ToolCall
from conftest import attach_mock_run_via_loop


def _make_generate_response(content="", tool_calls=None):
    """创建 mock provider.generate 的返回值"""
    return LLMResponse(
        content=content,
        tool_calls=tool_calls or [],
        usage=TokenUsage(),
        finish_reason="tool_calls" if tool_calls else "stop",
        model="gpt-4o",
    )


@pytest.fixture
def mock_router():
    router = MagicMock()
    router.data_store = None
    router.quota_check_enabled = False
    router.daily_limit = 20
    router.trace_enabled = False
    router.trace_max_age_days = 7
    router.config = None
    router._pending_tool_args = None
    router._pending_final_output = "ok"
    attach_mock_run_via_loop(router, final_output_attr="_pending_final_output")
    return router


@pytest.fixture
def agent(mock_router):
    return ScoringAgent(mock_router, timezone="Asia/Shanghai", max_tool_rounds=3)


class TestScoringToolPath:
    """测试 ScoringAgent 工具路径"""

    @pytest.mark.asyncio
    async def test_normal_tool_call_collection(self, agent, mock_router):
        """正常工具调用收集 → _extract_result 解析"""
        mock_router._pending_tool_args = '{"deltas": {"intimacy": 1.5, "passion": 0, "trust": 0.5, "secureness": 0}, "facts": {"爱好": "摄影"}}'

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
    async def test_llm_call_failure(self, agent, mock_router):
        """LLM 调用异常 → 返回 parse_error"""
        mock_router.run_via_loop.side_effect = Exception("服务不可用")

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
