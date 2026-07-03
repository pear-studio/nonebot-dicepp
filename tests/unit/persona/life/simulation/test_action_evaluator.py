"""单元测试: ActionEvaluator — 行动可行性评估的 LLM 异常状态检查 (R1)"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from plugins.DicePP.module.persona.life.action_evaluator import ActionEvaluator
from plugins.DicePP.module.persona.life.tool_loop import ToolResult


@pytest.fixture
def mock_store():
    store = MagicMock()
    store.get_character_state = AsyncMock()
    store.get_daily_events = AsyncMock(return_value=[])
    return store


@pytest.fixture
def mock_router():
    return MagicMock()


@pytest.fixture
def mock_config():
    config = MagicMock()
    config.suggest_action_evaluation_timeout = 30
    return config


@pytest.fixture
def evaluator(mock_store, mock_router, mock_config):
    return ActionEvaluator(store=mock_store, router=mock_router, config=mock_config)


class TestActionEvaluatorLLMAbnormal:
    """R1: 验证 _call_llm 在 ToolLoop 返回异常 final_reason 时显式返回错误"""

    @pytest.mark.asyncio
    async def test_evaluator_llm_abnormal_reason(self, evaluator, mock_store):
        """ToolLoop.execute 返回 final_reason="error" 时应返回 ("rejected", "LLM 协议错误")"""
        from plugins.DicePP.module.persona.data.models import CharacterState

        mock_store.get_character_state.return_value = CharacterState(
            energy=50, mood=50, health=50,
        )

        with patch(
            "plugins.DicePP.module.persona.life.tool_loop.ToolLoop"
        ) as mock_tool_loop_cls:
            mock_tl = MagicMock()
            mock_tl.execute = AsyncMock(return_value=ToolResult(
                new_messages=[],
                final_text="",
                final_reason="error",
            ))
            mock_tool_loop_cls.return_value = mock_tl

            result, reason = await evaluator.evaluate("去公园散步")
            assert result == "rejected"
            assert "LLM 协议错误" in reason, (
                f"期望 'LLM 协议错误' 但得到 '{reason}' —— "
                f"final_reason='error' 时应返回精确错误信息而非 'LLM 未生成评估结果'"
            )
