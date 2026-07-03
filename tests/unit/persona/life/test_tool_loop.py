"""测试 ToolLoop.execute() 及相关辅助函数"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from plugins.DicePP.module.persona.life.tool_loop import (
    ToolLoop,
    ToolResult,
    _build_collect_registry,
    _parse_tool_args,
)
from plugins.DicePP.module.persona.life.conversation import RunConfig


# ═══════════════════════════════════════════════════════════
# ToolLoop.execute() 集成测试 (R1)
# ═══════════════════════════════════════════════════════════

class TestToolLoopExecute:
    @pytest.mark.asyncio
    async def test_execute_chat_with_images(self):
        """R1: image_data_urls 正确传入 AgentRuntime.run_chat"""
        router = MagicMock()
        store = MagicMock()

        fake_result = MagicMock()
        fake_result.final_messages = []
        fake_result.final_text = "收到图片"
        fake_result.final_reason = "stop"
        fake_result.delivery_performed = False

        with patch(
            "plugins.DicePP.module.persona.life.tool_loop.AgentRuntime"
        ) as mock_runtime_cls:
            mock_runtime = MagicMock()
            mock_runtime.run_chat = AsyncMock(return_value=fake_result)
            mock_runtime_cls.return_value = mock_runtime

            loop = ToolLoop(router=router, store=store)
            config = RunConfig(
                mode="chat",
                image_data_urls=["data:image/png;base64,abc123"],
            )
            result = await loop.execute(
                messages=[{"role": "system", "content": "you are a bot"}],
                config=config,
            )

            # 断言 image_data_urls 被正确传入 run_chat
            call_kwargs = mock_runtime.run_chat.call_args.kwargs
            assert call_kwargs["image_data_urls"] == ["data:image/png;base64,abc123"]
            assert result.final_text == "收到图片"


class TestBuildCollectRegistry:
    """R1: _build_collect_registry 工具注册测试"""

    def test_includes_say_and_end_conversation(self):
        """say 和 end_conversation 应被注册到 collect registry 中"""
        from plugins.DicePP.module.persona.tools.collecting import (
            SAY_TOOL_DM,
            END_CONVERSATION_TOOL,
        )
        tools = [
            SAY_TOOL_DM.to_openai_format(),
            END_CONVERSATION_TOOL.to_openai_format(),
        ]
        reg = _build_collect_registry(tools)
        assert reg.get("say") is not None, (
            "say 工具未注册到 collect registry —— "
            "_ARGS_SCHEMA_MAP 可能缺少 'say' 条目"
        )
        assert reg.get("end_conversation") is not None, (
            "end_conversation 工具未注册到 collect registry —— "
            "_ARGS_SCHEMA_MAP 可能缺少 'end_conversation' 条目"
        )


# ═══════════════════════════════════════════════════════════
# _extract_tool_args 单元测试 (R2)
# ═══════════════════════════════════════════════════════════

class TestExtractToolArgs:
    """R2: _extract_tool_args 统一工具参数提取函数"""

    @pytest.fixture(autouse=True)
    def _import_extract(self):
        from plugins.DicePP.module.persona.life.tool_loop import (
            _extract_tool_args,
        )
        self._extract_tool_args = _extract_tool_args

    def test_extract_tool_args_anthropic_format(self):
        """Anthropic 格式: content list 含 tool_use 块"""
        msgs = [{
            "role": "assistant",
            "content": [{
                "type": "tool_use",
                "name": "say",
                "input": {"content": "hello"},
            }],
        }]
        result = self._extract_tool_args(msgs, {"say"})
        assert result == [("say", {"content": "hello"})]

    def test_extract_tool_args_openai_format(self):
        """OpenAI 格式: tool_calls 字段"""
        msgs = [{
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "function": {
                    "name": "record_event",
                    "arguments": '{"content": "event"}',
                },
            }],
        }]
        result = self._extract_tool_args(msgs, {"record_event"})
        assert result == [("record_event", {"content": "event"})]

    def test_extract_tool_args_filter_by_name(self):
        """按名称集合过滤，不匹配的工具被跳过"""
        msgs = [
            {"role": "assistant", "content": [{
                "type": "tool_use", "name": "say", "input": {"content": "A"},
            }]},
            {"role": "assistant", "content": [{
                "type": "tool_use", "name": "end_conversation", "input": {},
            }]},
        ]
        result = self._extract_tool_args(msgs, {"say"})
        assert len(result) == 1
        assert result[0] == ("say", {"content": "A"})

    def test_extract_tool_args_match_all_when_names_none(self):
        """tool_names=None 时匹配全部工具"""
        msgs = [
            {"role": "assistant", "content": [{
                "type": "tool_use", "name": "say", "input": {"content": "B"},
            }]},
            {"role": "assistant", "content": [{
                "type": "tool_use", "name": "end_conversation", "input": {},
            }]},
        ]
        result = self._extract_tool_args(msgs)
        assert len(result) == 2

    def test_extract_tool_args_skips_non_assistant(self):
        """跳过 user/system 消息"""
        msgs = [
            {"role": "user", "content": [{"type": "tool_use", "name": "say", "input": {}}]},
            {"role": "system", "content": "system msg"},
        ]
        result = self._extract_tool_args(msgs)
        assert result == []

    def test_extract_tool_args_empty_when_no_match(self):
        """无匹配工具时返回空列表"""
        msgs = [{
            "role": "assistant",
            "content": [{"type": "tool_use", "name": "other", "input": {}}],
        }]
        result = self._extract_tool_args(msgs, {"say"})
        assert result == []

    def test_backward_compat_parse_tool_args(self):
        """_parse_tool_args 保持向后兼容（单个工具名的薄封装）"""
        msgs = [{
            "role": "assistant",
            "content": [{
                "type": "tool_use", "name": "record_score",
                "input": {"score": 5},
            }],
        }]
        result = _parse_tool_args(msgs, "record_score")
        assert result == [{"score": 5}]
