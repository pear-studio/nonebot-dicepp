"""测试 ToolLoop.execute() 和 _filter_corrections 辅助函数"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from plugins.DicePP.module.persona.life.tool_loop import (
    ToolLoop,
    ToolResult,
    _filter_corrections,
)
from plugins.DicePP.module.persona.life.conversation import RunConfig


# ═══════════════════════════════════════════════════════════
# _filter_corrections 单元测试 (R6)
# ═══════════════════════════════════════════════════════════

class TestFilterCorrections:
    def test_removes_correction(self):
        """[系统指令] 前缀的 user 消息被过滤"""
        msgs = [
            {"role": "user", "content": "[系统指令] 请使用更温和的语气"},
            {"role": "assistant", "content": "好的"},
        ]
        result = _filter_corrections(msgs)
        assert len(result) == 1
        assert result[0]["content"] == "好的"

    def test_passes_normal(self):
        """普通 user/assistant 消息不被过滤"""
        msgs = [
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "你好！有什么可以帮你的？"},
        ]
        result = _filter_corrections(msgs)
        assert len(result) == 2

    def test_passes_list_content(self):
        """content 为 list 的消息不被过滤（如图片注入消息）"""
        msgs = [
            {"role": "user", "content": [{"type": "text", "text": "看看这张图"}]},
        ]
        result = _filter_corrections(msgs)
        assert len(result) == 1

    def test_passes_non_user(self):
        """assistant 角色的纠正消息不被过滤（仅过滤 role=user）"""
        msgs = [
            {"role": "assistant", "content": "[系统指令] 这不是用户发的"},
        ]
        result = _filter_corrections(msgs)
        assert len(result) == 1

    def test_empty_list(self):
        """空输入返回空列表"""
        result = _filter_corrections([])
        assert result == []


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
