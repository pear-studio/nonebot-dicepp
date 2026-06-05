"""format_message_results 单元测试"""
import pytest
from datetime import datetime

pytestmark = pytest.mark.unit


class _FakeMsg:
    """模拟 UnifiedMessage 的必要属性"""
    def __init__(self, user_id, role, content, display_name="", created_at=None):
        self.user_id = user_id
        self.role = role
        self.content = content
        self.display_name = display_name
        self.created_at = created_at


def test_empty_results():
    """空结果列表返回仅含表头的字符串"""
    from module.persona.tools.formatter import format_message_results
    result = format_message_results([])
    assert "参与者:" in result
    # 空列表只有表头行，无后续空行
    assert result.strip() == "参与者:"


def test_single_user():
    """单参与者正确匿名化"""
    from module.persona.tools.formatter import format_message_results
    msgs = [
        _FakeMsg("u1", "user", "你好", "小王", datetime(2026, 5, 21, 15, 0, 0)),
    ]
    result = format_message_results(msgs)
    assert "用户1" in result
    assert "小王" in result
    assert "你好" in result


def test_multiple_participants():
    """多参与者各自获得不同匿名 ID"""
    from module.persona.tools.formatter import format_message_results
    msgs = [
        _FakeMsg("u1", "user", "hello", "Alice", datetime(2026, 5, 21, 15, 0, 0)),
        _FakeMsg("u2", "user", "world", "Bob", datetime(2026, 5, 21, 15, 1, 0)),
    ]
    result = format_message_results(msgs)
    assert "用户1" in result
    assert "用户2" in result
    assert "Alice" in result
    assert "Bob" in result


def test_assistant_role_mapped_to_wo():
    """assistant 角色映射为 '我'"""
    from module.persona.tools.formatter import format_message_results
    msgs = [
        _FakeMsg("u1", "user", "你好", "小王", datetime(2026, 5, 21, 15, 0, 0)),
        _FakeMsg("bot", "assistant", "你好呀", "", datetime(2026, 5, 21, 15, 0, 1)),
    ]
    result = format_message_results(msgs)
    assert "我" in result
    # 参与者表头应显示 assistant -> 我
    assert "我 ->" not in result  # assistant 没有 display_name
    # speaker 栏应该是 "我"
    assert "[我]" in result


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
    """created_at 为 None 时显示空时间"""
    from module.persona.tools.formatter import format_message_results
    msgs = [
        _FakeMsg("u1", "user", "test", "小王", created_at=None),
    ]
    result = format_message_results(msgs)
    # 时间部分应为空字符串
    assert "[]" in result


def test_display_name_fallback():
    """display_name 缺失时回退到 user_id"""
    from module.persona.tools.formatter import format_message_results
    msgs = [
        _FakeMsg("u1", "user", "test", "", datetime(2026, 5, 21, 15, 0, 0)),
    ]
    result = format_message_results(msgs)
    # 参与者映射应显示 user_id
    assert "u1" in result
