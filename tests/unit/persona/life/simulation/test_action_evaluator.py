"""单元测试: ActionEvaluator — 行动可行性评估"""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from plugins.DicePP.module.persona.life.action_evaluator import ActionEvaluator
from plugins.DicePP.module.persona.life.tool_loop import ToolResult
from plugins.DicePP.module.persona.data.models import CharacterState


def _make_record_evaluation_tool_call(**kwargs):
    """构造模拟 LLM 返回的 record_evaluation 工具调用消息。"""
    return [{
        "role": "assistant",
        "content": [{
            "type": "tool_use",
            "name": "record_evaluation",
            "input": kwargs,
        }],
    }]


def _make_tool_result(new_messages, final_text="", final_reason="stop"):
    return ToolResult(
        new_messages=new_messages,
        final_text=final_text,
        final_reason=final_reason,
    )


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


class TestActionEvaluatorEvaluate:
    """ActionEvaluator.evaluate 主流程"""

    @pytest.mark.asyncio
    async def test_evaluate_normal(self, evaluator, mock_store):
        """正常评估路径应返回 ('approved', reason)"""
        mock_store.get_character_state.return_value = CharacterState(
            energy=50, mood=60, health=70,
        )

        with patch(
            "plugins.DicePP.module.persona.life.tool_loop.ToolLoop"
        ) as mock_tool_loop_cls:
            mock_tl = MagicMock()
            mock_tl.execute = AsyncMock(return_value=_make_tool_result(
                new_messages=_make_record_evaluation_tool_call(
                    result="approved", reason="角色状态良好，适合散步"
                ),
                final_reason="stop",
            ))
            mock_tool_loop_cls.return_value = mock_tl

            result, reason = await evaluator.evaluate("去公园散步")
            assert result == "approved"
            assert "状态良好" in reason

    @pytest.mark.asyncio
    async def test_evaluate_timeout(self, evaluator, mock_store):
        """ToolLoop.execute 超时应返回 ('rejected', 'LLM 调用失败')"""
        mock_store.get_character_state.return_value = CharacterState(
            energy=50, mood=50, health=50,
        )

        with patch(
            "plugins.DicePP.module.persona.life.tool_loop.ToolLoop"
        ) as mock_tool_loop_cls:
            mock_tl = MagicMock()
            mock_tl.execute = AsyncMock(side_effect=asyncio.TimeoutError("LLM timeout"))
            mock_tool_loop_cls.return_value = mock_tl

            result, reason = await evaluator.evaluate("去公园散步")
            assert result == "rejected"
            assert "LLM 调用失败" in reason

    @pytest.mark.asyncio
    async def test_evaluate_missing_fields(self, evaluator, mock_store):
        """角色状态字段缺失时应默认填充并正常返回评估结果"""
        mock_store.get_character_state.return_value = CharacterState(
            energy=None, mood=None, health=None,
        )

        with patch(
            "plugins.DicePP.module.persona.life.tool_loop.ToolLoop"
        ) as mock_tool_loop_cls:
            mock_tl = MagicMock()
            mock_tl.execute = AsyncMock(return_value=_make_tool_result(
                new_messages=_make_record_evaluation_tool_call(
                    result="approved", reason="可行"
                ),
                final_reason="stop",
            ))
            mock_tool_loop_cls.return_value = mock_tl

            result, reason = await evaluator.evaluate("去公园散步")
            assert result == "approved"


class TestActionEvaluatorLLMAbnormal:
    """R1: 验证 _call_llm 在 ToolLoop 返回异常 final_reason 时显式返回错误"""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("final_reason", [
        "error",
        "content_filter",
        "rate_limited",
        "invalid_response",
    ])
    async def test_evaluator_llm_abnormal_reason(self, evaluator, mock_store, final_reason):
        """ToolLoop.execute 返回异常 final_reason 时应返回 ('rejected', 'LLM 协议错误')"""
        mock_store.get_character_state.return_value = CharacterState(
            energy=50, mood=50, health=50,
        )

        with patch(
            "plugins.DicePP.module.persona.life.tool_loop.ToolLoop"
        ) as mock_tool_loop_cls:
            mock_tl = MagicMock()
            mock_tl.execute = AsyncMock(return_value=_make_tool_result(
                new_messages=[],
                final_text="",
                final_reason=final_reason,
            ))
            mock_tool_loop_cls.return_value = mock_tl

            result, reason = await evaluator.evaluate("去公园散步")
            assert result == "rejected"
            assert "LLM 协议错误" in reason, (
                f"期望 'LLM 协议错误' 但得到 '{reason}' —— "
                f"final_reason='{final_reason}' 时应返回精确错误信息而非 'LLM 未生成评估结果'"
            )
