"""
单元测试: ContextBuilder 格式化 / 截断功能
"""

import pytest
from datetime import datetime, timedelta

from plugins.DicePP.module.persona.character.models import Character, CharacterBook, LoreEntry
from plugins.DicePP.module.persona.data.models import UserProfile
from plugins.DicePP.module.persona.chat.context import ContextBuilder

# 今天日期，用于构造同日时间戳（format_timestamp 同日返回 HH:MM）
_TODAY = datetime.now().replace(hour=14, minute=0, second=0, microsecond=0)


def _dt(minute: int = 0) -> datetime:
    return _TODAY.replace(minute=minute)


# ═══════════════════════════════════════════════════════════════════
# 6.1 私聊格式化 _format_private_history
# ═══════════════════════════════════════════════════════════════════

class TestFormatPrivateHistory:

    def _make_character(self):
        return Character(name="苏晓", description="一个温柔的AI伴侣")

    def test_role_direct_mapping(self):
        builder = ContextBuilder(self._make_character())
        history = [
            {"role": "user", "content": "你好", "created_at": _dt(30)},
            {"role": "assistant", "content": "你好呀", "created_at": _dt(31)},
        ]
        result = builder._format_private_history(history)
        assert len(result) == 2
        assert result[0]["role"] == "user"
        assert result[1]["role"] == "assistant"

    def test_timestamp_prefix(self, monkeypatch):
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.chat.context.persona_wall_now",
            lambda tz: _TODAY,
        )
        builder = ContextBuilder(self._make_character())
        history = [
            {"role": "user", "content": "你好", "created_at": _dt(30)},
        ]
        result = builder._format_private_history(history)
        assert result[0]["content"].startswith("[14:30] ")

    def test_empty_list(self):
        builder = ContextBuilder(self._make_character())
        result = builder._format_private_history([])
        assert result == []

    def test_merge_consecutive_user_messages(self):
        """连续 user 消息合并为单条，保证 user/assistant 交替"""
        builder = ContextBuilder(self._make_character())
        history = [
            {"role": "user", "content": "消息A", "created_at": _dt(30)},
            {"role": "user", "content": "消息B", "created_at": _dt(31)},
            {"role": "assistant", "content": "回复", "created_at": _dt(32)},
            {"role": "user", "content": "消息C", "created_at": _dt(33)},
        ]
        result = builder._format_private_history(history)
        assert len(result) == 3
        assert result[0]["role"] == "user"
        assert "消息A" in result[0]["content"]
        assert "消息B" in result[0]["content"]
        assert "\n" in result[0]["content"]
        assert result[1]["role"] == "assistant"
        assert result[2]["role"] == "user"
        assert "消息C" in result[2]["content"]

    def test_merge_all_user_messages(self):
        """全部为 user 消息时合并为单条"""
        builder = ContextBuilder(self._make_character())
        history = [
            {"role": "user", "content": "A", "created_at": _dt(30)},
            {"role": "user", "content": "B", "created_at": _dt(31)},
            {"role": "user", "content": "C", "created_at": _dt(32)},
        ]
        result = builder._format_private_history(history)
        assert len(result) == 1
        assert result[0]["role"] == "user"
        assert "A" in result[0]["content"]
        assert "B" in result[0]["content"]
        assert "C" in result[0]["content"]

    def test_all_assistant_messages(self):
        """纯 assistant 消息不触发 buffer flush，逐条输出"""
        builder = ContextBuilder(self._make_character())
        history = [
            {"role": "assistant", "content": "回复1", "created_at": _dt(30)},
            {"role": "assistant", "content": "回复2", "created_at": _dt(31)},
        ]
        result = builder._format_private_history(history)
        assert len(result) == 2
        assert result[0]["role"] == "assistant"
        assert result[1]["role"] == "assistant"

    def test_nonstandard_role_treated_as_user(self):
        """非 assistant 的 role（如 system）被缓冲为 user"""
        builder = ContextBuilder(self._make_character())
        history = [
            {"role": "system", "content": "系统消息", "created_at": _dt(30)},
            {"role": "user", "content": "用户消息", "created_at": _dt(31)},
        ]
        result = builder._format_private_history(history)
        assert len(result) == 1
        assert result[0]["role"] == "user"
        assert "系统消息" in result[0]["content"]
        assert "用户消息" in result[0]["content"]

    def test_three_consecutive_users_then_assistant(self):
        """3 条连续 user 后接 assistant，user 全部合并"""
        builder = ContextBuilder(self._make_character())
        history = [
            {"role": "user", "content": "A", "created_at": _dt(30)},
            {"role": "user", "content": "B", "created_at": _dt(31)},
            {"role": "user", "content": "C", "created_at": _dt(32)},
            {"role": "assistant", "content": "回复", "created_at": _dt(33)},
        ]
        result = builder._format_private_history(history)
        assert len(result) == 2
        assert result[0]["role"] == "user"
        assert "A" in result[0]["content"]
        assert "B" in result[0]["content"]
        assert "C" in result[0]["content"]
        assert result[1]["role"] == "assistant"
        assert "回复" in result[1]["content"]


# ═══════════════════════════════════════════════════════════════════
# 6.2 群聊格式化 _format_group_history
# ═══════════════════════════════════════════════════════════════════

class TestFormatGroupHistory:

    def _make_character(self):
        return Character(name="苏晓", description="一个温柔的AI伴侣")

    def test_merge_consecutive_non_assistant(self):
        builder = ContextBuilder(self._make_character())
        history = [
            {"role": "user", "content": "你好", "speaker_name": "A",
             "created_at": _dt(30)},
            {"role": "user", "content": "嗨", "speaker_name": "B",
             "created_at": _dt(31)},
            {"role": "assistant", "content": "你们好呀",
             "created_at": _dt(31)},
        ]
        result = builder._format_group_history(history)
        assert len(result) == 2
        assert result[0]["role"] == "user"
        assert "[A] 你好" in result[0]["content"]
        assert "[B] 嗨" in result[0]["content"]
        assert result[1]["role"] == "assistant"
        assert "你们好呀" in result[1]["content"]

    def test_mixed_speakers_order(self):
        """A, B, assistant, A → merged(A+B), assistant, user(A)"""
        builder = ContextBuilder(self._make_character())
        history = [
            {"role": "user", "content": "msg1", "speaker_name": "A",
             "created_at": _dt(30)},
            {"role": "user", "content": "msg2", "speaker_name": "B",
             "created_at": _dt(31)},
            {"role": "assistant", "content": "reply",
             "created_at": _dt(32)},
            {"role": "user", "content": "msg3", "speaker_name": "A",
             "created_at": _dt(33)},
        ]
        result = builder._format_group_history(history)
        assert len(result) == 3
        assert result[0]["role"] == "user"
        assert result[1]["role"] == "assistant"
        assert result[2]["role"] == "user"
        assert "msg1" in result[0]["content"] and "msg2" in result[0]["content"]
        assert "reply" in result[1]["content"]
        assert "msg3" in result[2]["content"]

    def test_consecutive_assistants_not_merged(self):
        builder = ContextBuilder(self._make_character())
        history = [
            {"role": "assistant", "content": "reply1",
             "created_at": _dt(30)},
            {"role": "assistant", "content": "reply2",
             "created_at": _dt(31)},
        ]
        result = builder._format_group_history(history)
        assert len(result) == 2
        assert result[0]["role"] == "assistant"
        assert result[1]["role"] == "assistant"

    def test_speaker_name_fallback_to_system(self):
        builder = ContextBuilder(self._make_character())
        history = [
            {"role": "user", "content": "test",
             "created_at": _dt(30)},
        ]
        result = builder._format_group_history(history)
        assert "[系统] test" in result[0]["content"]

    def test_empty_list(self):
        builder = ContextBuilder(self._make_character())
        result = builder._format_group_history([])
        assert result == []


# ═══════════════════════════════════════════════════════════════════
# 6.3 轮次 + token 双重截断 truncate_by_turns
# ═══════════════════════════════════════════════════════════════════

class TestTruncateByTurns:

    def _make_character(self):
        return Character(name="苏晓", description="一个温柔的AI伴侣")

    def test_truncate_by_max_turns(self):
        """12 轮，max_turns=10 → 保留最近 10 轮"""
        builder = ContextBuilder(self._make_character())
        history = []
        for i in range(12):
            history.append({"role": "user", "content": f"u{i}"})
            history.append({"role": "assistant", "content": f"a{i}"})
        result = builder.truncate_by_turns(history, max_turns=10, max_tokens=10000)
        assert len(result) == 20
        assert result[0]["content"] == "u2"
        assert result[-1]["content"] == "a11"

    def test_truncate_by_token_budget(self):
        """token 预算超过时触发兜底"""
        builder = ContextBuilder(self._make_character())
        history = []
        for i in range(8):
            history.append({"role": "user", "content": "x" * 100})
            history.append({"role": "assistant", "content": "y" * 100})
        result = builder.truncate_by_turns(history, max_turns=10, max_tokens=200)
        assert len(result) >= 2
        assert len(result) <= 16

    def test_both_limits_token_wins(self):
        """token 早于 turns 触发截断"""
        builder = ContextBuilder(self._make_character())
        history = []
        for i in range(12):
            history.append({"role": "user", "content": "x" * 100})
            history.append({"role": "assistant", "content": "y" * 100})
        result = builder.truncate_by_turns(history, max_turns=10, max_tokens=150)
        assert len(result) < 20

    def test_full_turns_preserved(self):
        """不拆散 user/assistant 对"""
        builder = ContextBuilder(self._make_character())
        history = [
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "u2"},
            {"role": "assistant", "content": "a2"},
        ]
        result = builder.truncate_by_turns(history, max_turns=1, max_tokens=10000)
        assert len(result) == 2
        assert result[0]["role"] == "user" and result[0]["content"] == "u2"
        assert result[1]["role"] == "assistant" and result[1]["content"] == "a2"

    def test_orphan_user_preserved(self):
        """末尾孤立 user 保留"""
        builder = ContextBuilder(self._make_character())
        history = [
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "u2"},
        ]
        result = builder.truncate_by_turns(history, max_turns=2, max_tokens=10000)
        assert result[-1]["role"] == "user"
        assert result[-1]["content"] == "u2"

    def test_empty_history(self):
        builder = ContextBuilder(self._make_character())
        result = builder.truncate_by_turns([], max_turns=10, max_tokens=4000)
        assert result == []

    def test_leading_assistant_stripped_and_preserved(self):
        """R1: 历史以 assistant 开头时剥离并在截断后 prepend"""
        builder = ContextBuilder(self._make_character())
        history = [
            {"role": "assistant", "content": "leading_a"},
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "a1"},
        ]
        result = builder.truncate_by_turns(history, max_turns=2, max_tokens=10000)
        assert result[0]["role"] == "assistant"
        assert result[0]["content"] == "leading_a"
        assert len(result) == 3

    def test_trailing_orphan_assistant_preserved(self):
        """R1: work 长度奇数时末尾孤立 assistant 被兜底保留"""
        builder = ContextBuilder(self._make_character())
        history = [
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "u2"},
            {"role": "assistant", "content": "a2"},
            {"role": "assistant", "content": "orphan_a"},
        ]
        result = builder.truncate_by_turns(history, max_turns=5, max_tokens=10000)
        assert result[-1]["role"] == "assistant"
        assert result[-1]["content"] == "orphan_a"


# ═══════════════════════════════════════════════════════════════════
# format_history 统一入口
# ═══════════════════════════════════════════════════════════════════

class TestFormatHistory:

    def _make_character(self):
        return Character(name="苏晓", description="一个温柔的AI伴侣")

    def test_is_group_false_dispatches_private(self, monkeypatch):
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.chat.context.persona_wall_now",
            lambda tz: _TODAY,
        )
        builder = ContextBuilder(self._make_character())
        history = [
            {"role": "user", "content": "hi", "created_at": _dt(30)},
        ]
        result = builder.format_history(history, is_group=False)
        assert len(result) == 1
        assert result[0]["role"] == "user"
        assert result[0]["content"].startswith("[14:30] ")

    def test_is_group_true_dispatches_group(self):
        builder = ContextBuilder(self._make_character())
        history = [
            {"role": "user", "content": "hi", "speaker_name": "A",
             "created_at": _dt(30)},
        ]
        result = builder.format_history(history, is_group=True)
        assert len(result) == 1
        assert result[0]["role"] == "user"
        assert "[A]" in result[0]["content"]
