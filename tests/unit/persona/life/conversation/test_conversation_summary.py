"""
Conversation 摘要模块测试（阶段 3b）。

覆盖：
- FakeSummarizer 正确返回固定文本
- ProviderSummarizer 调用链（mock router/provider）
- 失败不抛异常
- prompt 结构
- SUMMARY_MIN_MESSAGES 阈值
"""
import pytest
from unittest.mock import AsyncMock, MagicMock

from plugins.DicePP.module.persona.life.conversation import DANGLING_REF_FALLBACK, NOTIFICATION_PREFIX
from plugins.DicePP.module.persona.life.conversation_summary import (
    FakeSummarizer,
    ProviderSummarizer,
    Summarizer,
    SUMMARY_MIN_MESSAGES,
    _build_summary_prompt,
)


class TestFakeSummarizer:
    """FakeSummarizer 测试 double 验证"""

    @pytest.mark.asyncio
    async def test_returns_fixed_text(self):
        summarizer = FakeSummarizer(return_text="hello summary")
        result = await summarizer.generate_summary([{"role": "user", "content": "hi"}])
        assert result == "hello summary"
        assert len(summarizer.called_with) == 1

    @pytest.mark.asyncio
    async def test_raises_when_fail_set(self):
        summarizer = FakeSummarizer(fail=True)
        with pytest.raises(RuntimeError, match="fake summarizer failure"):
            await summarizer.generate_summary([{"role": "user", "content": "hi"}])

    @pytest.mark.asyncio
    async def test_empty_messages_returns_empty(self):
        summarizer = FakeSummarizer()
        result = await summarizer.generate_summary([])
        assert result == "fake summary text"

    def test_implements_summarizer_protocol(self):
        summarizer = FakeSummarizer()
        assert isinstance(summarizer, Summarizer)


class TestBuildSummaryPrompt:
    """_build_summary_prompt 验证"""

    def test_normal_messages(self):
        messages = [
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "你好呀"},
        ]
        prompt = _build_summary_prompt(messages)
        assert len(prompt) == 2
        assert prompt[0]["role"] == "system"
        assert "摘要助手" in prompt[0]["content"]
        assert prompt[1]["role"] == "user"
        assert "你好" in prompt[1]["content"]
        assert "你好呀" in prompt[1]["content"]

    def test_skips_dangling_ref(self):
        """悬空 ref（content=DANGLING_REF_FALLBACK）被跳过。
        S3: 显式否定断言——DANGLING_REF_FALLBACK 文本不出现在输出中。"""
        messages = [
            {"role": "user", "entry_type": "ref", "content": DANGLING_REF_FALLBACK},
            {"role": "assistant", "content": "正常回复"},
        ]
        prompt = _build_summary_prompt(messages)
        # 悬空 ref 被跳过，assistant 消息保留
        assert "正常回复" in prompt[1]["content"]
        # S3: 否定断言——DANGLING_REF_FALLBACK 不出现在 prompt 文本中
        assert DANGLING_REF_FALLBACK not in prompt[1]["content"]

    def test_empty_messages_produces_empty_placeholder(self):
        prompt = _build_summary_prompt([])
        assert "(空)" in prompt[1]["content"]

    def test_skips_notification_prefix_entries(self):
        """S4 回归: [通知] 前缀的摘要/系统消息不进入摘要输入。
        避免 summary-of-summary 漂移退化。"""
        messages = [
            {"role": "user", "content": f"{NOTIFICATION_PREFIX} 之前的对话摘要：上轮内容是闲聊"},
            {"role": "user", "content": "用户正常消息"},
            {"role": "assistant", "content": "角色正常回复"},
        ]
        prompt = _build_summary_prompt(messages)
        result = prompt[1]["content"]
        # S4: 否定断言——[通知] 前缀的条目文本不出现在 prompt 中
        assert "之前的对话摘要" not in result
        assert "上轮内容是闲聊" not in result
        # 正常消息应保留
        assert "用户正常消息" in result
        assert "角色正常回复" in result

    def test_all_notification_messages_returns_empty_placeholder(self):
        """所有消息均为 [通知] 前缀时输出 (空)。"""
        messages = [
            {"role": "user", "content": f"{NOTIFICATION_PREFIX} 系统通知1"},
            {"role": "assistant", "content": f"{NOTIFICATION_PREFIX} 系统通知2"},
        ]
        prompt = _build_summary_prompt(messages)
        assert "(空)" in prompt[1]["content"]

    def test_skips_empty_assistant_content(self):
        """F3: content 去空白后为空的 assistant 条目被跳过，不产出 '角色：' 空行。"""
        messages = [
            {"role": "user", "content": "用户正常消息"},
            {"role": "assistant", "content": ""},         # 纯空内容，应跳过
            {"role": "assistant", "content": "   "},       # 纯空白，应跳过
            {"role": "assistant", "content": "角色正常回复"},  # 非空，应保留
        ]
        prompt = _build_summary_prompt(messages)
        user_part = prompt[1]["content"]
        # 非空 assistant 条目仍出现
        assert "角色正常回复" in user_part
        # 不应出现空行形式的 "角色："（如 "角色：\n" 或 "角色：" 后无内容）
        assert "角色：\n" not in user_part, \
            "不应出现 '角色：\\n' 空行"
        # 用户消息正常保留
        assert "用户正常消息" in user_part

    def test_keeps_non_empty_assistant_content(self):
        """非空 assistant 条目不受影响，正常出现在 prompt 中。"""
        messages = [
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "你好呀"},   # 非空，应保留
        ]
        prompt = _build_summary_prompt(messages)
        user_part = prompt[1]["content"]
        assert "你好呀" in user_part
        assert "角色：你好呀" in user_part

class TestProviderSummarizer:
    """ProviderSummarizer 调用链测试（mock router/provider）"""

    @pytest.mark.asyncio
    async def test_successful_summary(self):
        mock_provider = MagicMock()
        mock_resp = MagicMock()
        mock_resp.content = "这是摘要内容。"
        mock_resp.model = "test"
        mock_provider.generate = AsyncMock(return_value=mock_resp)

        mock_router = MagicMock()
        mock_router.build_candidates.return_value = ["key1"]
        mock_router.get_model_provider.return_value = mock_provider

        summarizer = ProviderSummarizer(mock_router)
        result = await summarizer.generate_summary([
            {"role": "user", "content": "测试"},
            {"role": "assistant", "content": "回复"},
        ])
        assert result == "这是摘要内容。"
        mock_provider.generate.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_empty_messages_returns_empty(self):
        summarizer = ProviderSummarizer(MagicMock())
        result = await summarizer.generate_summary([])
        assert result == ""

    @pytest.mark.asyncio
    async def test_provider_failure_returns_empty(self):
        mock_provider = MagicMock()
        mock_provider.generate = AsyncMock(side_effect=RuntimeError("API down"))

        mock_router = MagicMock()
        mock_router.build_candidates.return_value = ["key1"]
        mock_router.get_model_provider.return_value = mock_provider

        summarizer = ProviderSummarizer(mock_router)
        # 异常被吞，返回空串
        result = await summarizer.generate_summary([
            {"role": "user", "content": "hi"},
        ])
        assert result == ""

    @pytest.mark.asyncio
    async def test_empty_response_content_returns_empty(self):
        mock_provider = MagicMock()
        mock_resp = MagicMock()
        mock_resp.content = ""
        mock_resp.model = "test"
        mock_provider.generate = AsyncMock(return_value=mock_resp)

        mock_router = MagicMock()
        mock_router.build_candidates.return_value = ["key1"]
        mock_router.get_model_provider.return_value = mock_provider

        summarizer = ProviderSummarizer(mock_router)
        result = await summarizer.generate_summary([
            {"role": "user", "content": "hi"},
        ])
        assert result == ""


class TestSummaryMinMessages:
    """SUMMARY_MIN_MESSAGES 阈值验证"""

    def test_constant_exists(self):
        assert SUMMARY_MIN_MESSAGES == 4
