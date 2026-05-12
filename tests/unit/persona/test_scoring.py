"""
单元测试: ScoringAgent 工具路径

覆盖 CollectExecutor 收集、fallback、JSON 解析失败三种路径。
"""

import pytest
from unittest.mock import MagicMock, AsyncMock

from plugins.DicePP.module.persona.chat.scoring import ScoringAgent
from plugins.DicePP.module.persona.data.models import ScoreDeltas


def _make_side_effect(args_json: str, tool_name: str = "score_relationship"):
    """创建 router.generate 的 side_effect，调用 tool_executor 填充 CollectExecutor"""
    async def side_effect(**kwargs):
        tool_executor = kwargs.get("tool_executor")
        if tool_executor:
            tc = {
                "id": "tc_1",
                "name": tool_name,
                "arguments": args_json,
            }
            await tool_executor([tc])
        return "", {}
    return side_effect


@pytest.fixture
def mock_router():
    router = MagicMock()
    router.generate = AsyncMock()
    return router


@pytest.fixture
def agent(mock_router):
    return ScoringAgent(mock_router, timezone="Asia/Shanghai", max_tool_rounds=3)


class TestScoringToolPath:
    """测试 ScoringAgent 工具路径"""

    @pytest.mark.asyncio
    async def test_normal_tool_call_collection(self, agent, mock_router):
        """正常工具调用收集 → _extract_result 解析"""
        mock_router.generate.side_effect = _make_side_effect(
            '{"deltas": {"intimacy": 1.5, "passion": 0, "trust": 0.5, "secureness": 0}, '
            '"facts": {"爱好": "摄影"}}',
        )

        result = await agent.batch_analyze(
            messages=[
                {"role": "user", "content": "你好"},
                {"role": "assistant", "content": "你好！"},
            ],
        )

        assert result.deltas.intimacy == 1.5
        assert result.deltas.passion == 0.0
        assert result.deltas.trust == 0.5
        assert result.facts == {"爱好": "摄影"}
        assert result.parse_error == ""
        mock_router.generate.assert_called_once()
        call_kwargs = mock_router.generate.call_args.kwargs
        assert call_kwargs["tools"] is not None
        assert call_kwargs["tool_executor"] is not None

    @pytest.mark.asyncio
    async def test_empty_collected_fallback_to_parse_response(self, agent, mock_router):
        """executor.collected 为空 → fallback 到 _parse_response(content)"""
        response_json = (
            '{"deltas": {"intimacy": -1.0, "passion": 0, "trust": 0, "secureness": 0}, '
            '"facts": {}}'
        )

        async def no_tool_call(**kwargs):
            return response_json, {}

        mock_router.generate.side_effect = no_tool_call

        result = await agent.batch_analyze(
            messages=[
                {"role": "user", "content": "test"},
                {"role": "assistant", "content": "ok"},
            ],
        )

        assert result.deltas.intimacy == -1.0
        assert result.parse_error == ""

    @pytest.mark.asyncio
    async def test_json_parse_failure(self, agent, mock_router):
        """arguments JSON 解析失败 → parse_error 非空"""
        mock_router.generate.side_effect = _make_side_effect(
            "not valid json{{{",
        )

        result = await agent.batch_analyze(
            messages=[
                {"role": "user", "content": "test"},
                {"role": "assistant", "content": "ok"},
            ],
        )

        assert result.parse_error != ""

    @pytest.mark.asyncio
    async def test_llm_call_failure(self, agent, mock_router):
        """LLM 调用异常 → 返回 parse_error"""
        mock_router.generate.side_effect = Exception("服务不可用")

        result = await agent.batch_analyze(
            messages=[
                {"role": "user", "content": "test"},
                {"role": "assistant", "content": "ok"},
            ],
        )

        assert "LLM 调用失败" in result.parse_error
        assert result.deltas == ScoreDeltas()
        assert result.facts == {}

    @pytest.mark.asyncio
    async def test_empty_collected_and_empty_content(self, agent, mock_router):
        """collected 为空且 content 为空 → fallback 返回空结果"""
        async def no_tool_no_content(**kwargs):
            return "", {}

        mock_router.generate.side_effect = no_tool_no_content

        result = await agent.batch_analyze(
            messages=[
                {"role": "user", "content": "test"},
                {"role": "assistant", "content": "ok"},
            ],
        )

        assert result.deltas == ScoreDeltas()
        assert result.parse_error != ""


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
