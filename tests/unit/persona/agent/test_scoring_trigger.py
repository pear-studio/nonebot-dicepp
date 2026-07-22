"""ScoringTrigger CH6 重试逻辑单元测试

覆盖 _handle_scoring_failure 的状态转换:
- batch_analyze 异常 → 保留 pending + 递增 retry_count
- parse_error → 同样走重试逻辑
- 连续 3 次失败 → 丢弃 pending + 清除 retry_count
- 评分成功 → 重置 retry_count
"""
import pytest
from unittest.mock import AsyncMock, MagicMock

from module.persona.chat.scoring_trigger import ScoringTrigger
from module.persona.chat.chat_config import ChatConfig
from module.persona.data.models import RelationshipState, ScoreDeltas


def _make_trigger(*, scoring_agent=None, scoring_interval=2):
    """构造最小可运行 ScoringTrigger"""
    store = AsyncMock()
    store.get_relationship = AsyncMock(return_value=RelationshipState(user_id="u1"))
    store.init_relationship = AsyncMock(return_value=RelationshipState(user_id="u1"))
    store.get_familiarity_daily = AsyncMock(return_value=0.0)
    store.add_familiarity_daily = AsyncMock(return_value=0.6)
    store.update_relationship = AsyncMock()
    store.add_score_event = AsyncMock()
    store.get_user_profile = AsyncMock(return_value=None)
    store.save_user_profile = AsyncMock()
    store.record_scoring_failure = AsyncMock()

    character = MagicMock()

    config = ChatConfig(scoring_interval=scoring_interval, timezone="")

    return ScoringTrigger(
        store=store,
        scoring_agent=scoring_agent,
        decay_calculator=None,
        character=character,
        config=config,
    )


class TestScoringRetry:
    """CH6 重试/丢弃逻辑测试"""

    @pytest.mark.asyncio
    async def test_exception_retains_pending_and_increments_retry(self):
        """batch_analyze 首次异常 → 保留 pending，retry_count=1"""
        scoring_agent = MagicMock()
        scoring_agent.batch_analyze = AsyncMock(side_effect=RuntimeError("LLM 调用失败"))

        trigger = _make_trigger(scoring_agent=scoring_agent, scoring_interval=1)

        # 触发 on_interaction → 2 条消息满 threshold → 调 batch_analyze → 异常
        await trigger.on_interaction("u1", "g1", "msg1", "reply1")

        key = ("u1", "g1")
        assert len(trigger._pending_messages.get(key, [])) == 2  # 保留
        assert trigger._retry_count.get(key, 0) == 1

    @pytest.mark.asyncio
    async def test_parse_error_retains_pending_and_increments_retry(self):
        """parse_error 非空 → 保留 pending，retry_count=1"""
        from module.persona.chat.scoring import ScoringAnalysisResult

        scoring_agent = MagicMock()
        scoring_agent.batch_analyze = AsyncMock(return_value=ScoringAnalysisResult(
            deltas=ScoreDeltas(), facts={},
            parse_error="JSON 解析失败",
        ))

        trigger = _make_trigger(scoring_agent=scoring_agent, scoring_interval=1)
        await trigger.on_interaction("u1", "g1", "msg1", "reply1")

        key = ("u1", "g1")
        assert len(trigger._pending_messages.get(key, [])) == 2  # 保留
        assert trigger._retry_count.get(key, 0) == 1

    @pytest.mark.asyncio
    async def test_three_consecutive_failures_discard_pending(self):
        """连续 3 次失败 → 丢弃 pending + 清除 retry_count"""
        scoring_agent = MagicMock()
        scoring_agent.batch_analyze = AsyncMock(side_effect=RuntimeError("LLM 调用失败"))

        trigger = _make_trigger(scoring_agent=scoring_agent, scoring_interval=1)

        # 第 1 次失败
        await trigger.on_interaction("u1", "g1", "msg1", "reply1")
        await trigger.on_interaction("u1", "g1", "msg2", "reply2")
        key = ("u1", "g1")
        assert key in trigger._pending_messages  # 尚未丢弃

        # 第 2 次失败
        await trigger.on_interaction("u1", "g1", "msg3", "reply3")
        await trigger.on_interaction("u1", "g1", "msg4", "reply4")
        assert key in trigger._pending_messages  # 保留

        # 第 3 次失败 → 丢弃
        await trigger.on_interaction("u1", "g1", "msg5", "reply5")
        await trigger.on_interaction("u1", "g1", "msg6", "reply6")
        assert key not in trigger._pending_messages  # 已丢弃
        assert key not in trigger._retry_count       # 已清除

    @pytest.mark.asyncio
    async def test_parse_error_three_failures_discards(self):
        """parse_error 连续 3 次 → 丢弃"""
        from module.persona.chat.scoring import ScoringAnalysisResult

        scoring_agent = MagicMock()
        scoring_agent.batch_analyze = AsyncMock(return_value=ScoringAnalysisResult(
            deltas=ScoreDeltas(), facts={},
            parse_error="JSON 解析失败",
        ))

        trigger = _make_trigger(scoring_agent=scoring_agent, scoring_interval=1)

        for i in range(3):
            await trigger.on_interaction("u1", "g1", f"msg{i*2+1}", f"reply{i*2+1}")
            await trigger.on_interaction("u1", "g1", f"msg{i*2+2}", f"reply{i*2+2}")

        key = ("u1", "g1")
        assert key not in trigger._pending_messages
        assert key not in trigger._retry_count

    @pytest.mark.asyncio
    async def test_success_resets_retry_count(self):
        """成功后 retry_count 清零"""
        from module.persona.chat.scoring import ScoringAnalysisResult

        scoring_agent = MagicMock()
        # 第一次失败
        scoring_agent.batch_analyze = AsyncMock(side_effect=[
            RuntimeError("失败1"),
            ScoringAnalysisResult(deltas=ScoreDeltas(intimacy=1.0), facts={}, parse_error=""),
        ])

        trigger = _make_trigger(scoring_agent=scoring_agent, scoring_interval=1)

        # 第 1 次：失败，retry_count=1
        await trigger.on_interaction("u1", "g1", "msg1", "reply1")
        key = ("u1", "g1")
        assert trigger._retry_count.get(key, 0) == 1

        # 第 2 次：成功，retry_count 清零，pending 清除
        await trigger.on_interaction("u1", "g1", "msg2", "reply2")
        assert trigger._retry_count.get(key, 0) == 0
        assert key not in trigger._pending_messages

    @pytest.mark.asyncio
    async def test_messages_accumulate_during_retry(self):
        """重试期间新消息继续在 pending 中累积"""
        scoring_agent = MagicMock()
        scoring_agent.batch_analyze = AsyncMock(side_effect=RuntimeError("LLM 失败"))

        trigger = _make_trigger(scoring_agent=scoring_agent, scoring_interval=1)

        # 第 1 次触发（2 条消息）
        await trigger.on_interaction("u1", "g1", "msg1", "reply1")
        key = ("u1", "g1")
        # 此时 pending 中有 2 条（scoring_interval=1 → threshold=2，触发后保留）
        # 但注意：触发后 pending 被保留（未 pop），新消息继续追加
        await trigger.on_interaction("u1", "g1", "msg2", "reply2")
        # 现在 pending 中有 4 条（保留的 2 条 + 新 2 条）

        pending_count = len(trigger._pending_messages.get(key, []))
        assert pending_count == 4  # 消息持续累积


class TestScoringWarnPending:
    """warn_pending 状态转换测试 (Q122)"""

    @pytest.mark.asyncio
    async def test_warning_issued_sets_warn_pending(self):
        """warning_issued=True 且无扣分时设置 warn_pending"""
        from module.persona.chat.scoring import ScoringAnalysisResult

        scoring_agent = MagicMock()
        scoring_agent.batch_analyze = AsyncMock(return_value=ScoringAnalysisResult(
            deltas=ScoreDeltas(reputation_delta=0.0, warning_issued=True),
            facts={}, parse_error="",
        ))

        trigger = _make_trigger(scoring_agent=scoring_agent, scoring_interval=1)
        # 触发评分: 2 条消息达到 threshold
        await trigger.on_interaction("u1", "g1", "msg1", "reply1")

        assert trigger._warn_pending.get("u1", False) is True

    @pytest.mark.asyncio
    async def test_negative_reputation_clears_warn_pending(self):
        """reputation_delta<0 时清除 warn_pending（即使 warning_issued 也为 True）"""
        from module.persona.chat.scoring import ScoringAnalysisResult

        scoring_agent = MagicMock()
        scoring_agent.batch_analyze = AsyncMock(return_value=ScoringAnalysisResult(
            deltas=ScoreDeltas(reputation_delta=-5.0, warning_issued=True),
            facts={}, parse_error="",
        ))

        trigger = _make_trigger(scoring_agent=scoring_agent, scoring_interval=1)
        trigger._warn_pending["u1"] = True  # 预设 warn_pending
        await trigger.on_interaction("u1", "g1", "msg1", "reply1")

        assert "u1" not in trigger._warn_pending

    @pytest.mark.asyncio
    async def test_normal_interaction_clears_warn_pending(self):
        """正常互动（无 warning、无扣分）清除 warn_pending"""
        from module.persona.chat.scoring import ScoringAnalysisResult

        scoring_agent = MagicMock()
        scoring_agent.batch_analyze = AsyncMock(return_value=ScoringAnalysisResult(
            deltas=ScoreDeltas(reputation_delta=0.0, warning_issued=False),
            facts={}, parse_error="",
        ))

        trigger = _make_trigger(scoring_agent=scoring_agent, scoring_interval=1)
        trigger._warn_pending["u1"] = True  # 预设 warn_pending
        await trigger.on_interaction("u1", "g1", "msg1", "reply1")

        assert "u1" not in trigger._warn_pending

    @pytest.mark.asyncio
    async def test_warn_pending_passed_to_batch_analyze(self):
        """warn_pending=True 时传入 batch_analyze 参数"""
        from module.persona.chat.scoring import ScoringAnalysisResult

        scoring_agent = MagicMock()
        scoring_agent.batch_analyze = AsyncMock(return_value=ScoringAnalysisResult(
            deltas=ScoreDeltas(reputation_delta=0.0, warning_issued=True),
            facts={}, parse_error="",
        ))

        trigger = _make_trigger(scoring_agent=scoring_agent, scoring_interval=1)
        trigger._warn_pending["u1"] = True  # 预设 warn_pending
        await trigger.on_interaction("u1", "g1", "msg1", "reply1")

        # 验证 batch_analyze 被调用且 warn_pending=True
        call_kwargs = scoring_agent.batch_analyze.call_args.kwargs
        assert call_kwargs.get("warn_pending") is True

    @pytest.mark.asyncio
    async def test_no_warn_pending_passed_as_false(self):
        """warn_pending 不存在时传入 batch_analyze 为 False"""
        from module.persona.chat.scoring import ScoringAnalysisResult

        scoring_agent = MagicMock()
        scoring_agent.batch_analyze = AsyncMock(return_value=ScoringAnalysisResult(
            deltas=ScoreDeltas(reputation_delta=0.0, warning_issued=False),
            facts={}, parse_error="",
        ))

        trigger = _make_trigger(scoring_agent=scoring_agent, scoring_interval=1)
        await trigger.on_interaction("u1", "g1", "msg1", "reply1")

        call_kwargs = scoring_agent.batch_analyze.call_args.kwargs
        assert call_kwargs.get("warn_pending") is False


class TestBuildConversationDigest:
    """_build_conversation_digest 格式与截断测试 (Q123)"""

    def test_digest_mixes_roles(self):
        """user/assistant 消息正确标记为 U/A 前缀"""
        history = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "world"},
        ]
        result = ScoringTrigger._build_conversation_digest(history)
        assert result == "U: hello; A: world"

    def test_digest_truncates_long_content(self):
        """超过 DIGEST_MAX_CHARS(80) 的内容被截断并追加 ..."""
        history = [
            {"role": "user", "content": "X" * 100},
        ]
        result = ScoringTrigger._build_conversation_digest(history)
        assert result == "U: " + "X" * 77 + "..."
        assert len(result) == 3 + 80  # "U: " prefix + 80 chars

    def test_digest_only_last_six_messages(self):
        """超过 DIGEST_MAX_MESSAGES(6) 时只保留最后 6 条"""
        history = [{"role": "user", "content": f"msg{i}"} for i in range(10)]
        result = ScoringTrigger._build_conversation_digest(history)
        # 只应出现 msg4..msg9（最后 6 条）
        assert "msg0" not in result
        assert "msg3" not in result
        assert "msg4" in result
        assert "msg9" in result
        assert result.count("U:") == 6

    def test_digest_unknown_role_uses_question_mark(self):
        """未知 role 映射为 ? 前缀"""
        history = [
            {"role": "unknown_role", "content": "test"},
        ]
        result = ScoringTrigger._build_conversation_digest(history)
        assert result == "?: test"

    def test_digest_empty_history(self):
        """空列表返回空字符串"""
        assert ScoringTrigger._build_conversation_digest([]) == ""

    def test_digest_tool_and_system_roles(self):
        """tool=T system=S 前缀映射正确"""
        history = [
            {"role": "system", "content": "init"},
            {"role": "tool", "content": "result"},
        ]
        result = ScoringTrigger._build_conversation_digest(history)
        assert result == "S: init; T: result"
