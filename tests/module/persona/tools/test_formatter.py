"""format_message_results 单元测试"""
import pytest
from datetime import datetime

pytestmark = pytest.mark.unit


class _FakeMsg:
    """模拟 UnifiedMessage 的必要属性"""
    def __init__(self, user_id, role, content, display_name="", created_at=None, type="chat"):
        self.user_id = user_id
        self.role = role
        self.content = content
        self.display_name = display_name
        self.created_at = created_at
        self.type = type


def test_empty_results():
    """空结果不再生成冗余参与者表头。"""
    from module.persona.tools.formatter import format_message_results
    result = format_message_results([])
    assert result == ""


def test_single_user():
    """玩家行同时暴露稳定账号和可读昵称。"""
    from module.persona.tools.formatter import format_message_results
    msgs = [
        _FakeMsg("u1", "user", "你好", "小王", datetime(2026, 5, 21, 15, 0, 0)),
    ]
    result = format_message_results(msgs)
    assert result == "[2026-05-21 15:00:00] [玩家] [uid: u1] [昵称: 小王] 你好"


def test_multiple_participants():
    """多参与者直接按 uid 区分，不生成易漂移的玩家序号映射。"""
    from module.persona.tools.formatter import format_message_results
    msgs = [
        _FakeMsg("u1", "user", "hello", "Alice", datetime(2026, 5, 21, 15, 0, 0)),
        _FakeMsg("u2", "user", "world", "Bob", datetime(2026, 5, 21, 15, 1, 0)),
    ]
    result = format_message_results(msgs)
    assert "参与者:" not in result
    assert "玩家1" not in result
    assert "[2026-05-21 15:00:00] [玩家] [uid: u1] [昵称: Alice] hello" in result
    assert "[2026-05-21 15:01:00] [玩家] [uid: u2] [昵称: Bob] world" in result


def test_assistant_role_mapped_to_wo():
    """assistant 角色映射为 '我'"""
    from module.persona.tools.formatter import format_message_results
    msgs = [
        _FakeMsg("u1", "user", "你好", "小王", datetime(2026, 5, 21, 15, 0, 0)),
        _FakeMsg("bot", "assistant", "你好呀", "", datetime(2026, 5, 21, 15, 0, 1)),
    ]
    result = format_message_results(msgs)
    assert "[2026-05-21 15:00:01] [我] 你好呀" in result


def test_content_truncation():
    """超过 max_chars 的内容被截断"""
    from module.persona.tools.formatter import format_message_results
    long_content = "A" * 200
    msgs = [
        _FakeMsg("u1", "user", long_content, "小王", datetime(2026, 5, 21, 15, 0, 0)),
    ]
    result = format_message_results(msgs, max_chars=10)
    # 内容应被截断为 10 字符 + "..."
    assert "A" * 10 + "..." in result
    assert long_content not in result


def test_missing_created_at():
    """created_at 为 None 时省略时间，但保留明确的发言者。"""
    from module.persona.tools.formatter import format_message_results
    msgs = [
        _FakeMsg("u1", "user", "test", "小王", created_at=None),
    ]
    result = format_message_results(msgs)
    assert "[玩家] [uid: u1] [昵称: 小王] test" in result
    assert "[]" not in result


def test_display_name_fallback():
    """display_name 缺失时回退到 user_id"""
    from module.persona.tools.formatter import format_message_results
    msgs = [
        _FakeMsg("u1", "user", "test", "", datetime(2026, 5, 21, 15, 0, 0)),
    ]
    result = format_message_results(msgs)
    assert result == "[2026-05-21 15:00:00] [玩家] [uid: u1] [昵称: u1] test"


def test_persisted_system_event_uses_event_line_even_with_user_role():
    """事件类型优先于 role，避免持久事件被误标成玩家发言。"""
    from module.persona.tools.formatter import format_message_results
    msgs = [
        _FakeMsg(
            "u1", "user", "[uid: u1] [昵称: 小王] 查询今日运势",
            "小王", datetime(2026, 5, 21, 18, 0, 0), type="system_notice",
        ),
    ]

    assert format_message_results(msgs) == (
        "[2026-05-21 18:00:00] [事件] "
        "[uid: u1] [昵称: 小王] 查询今日运势"
    )
