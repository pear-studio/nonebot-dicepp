"""
MessageType 枚举的单元测试（新增 AMBIENT / PROACTIVE）
"""
import pytest
from plugins.DicePP.core.message_types import MessageType


class TestMessageType:
    """枚举值正确性"""

    def test_all_values(self):
        assert MessageType.CHAT.value == "chat"
        assert MessageType.COMMAND.value == "command"
        assert MessageType.LOG_CONTROL.value == "log_control"
        assert MessageType.PROACTIVE.value == "proactive"
        assert MessageType.AMBIENT.value == "ambient"
        assert MessageType.SYSTEM_NOTICE.value == "system_notice"
        assert MessageType.SYSTEM_LOG.value == "system_log"

    def test_total_count(self):
        assert len(MessageType) == 7


class TestFromStr:
    """from_str 解析逻辑"""

    def test_known_values(self):
        assert MessageType.from_str("chat") == MessageType.CHAT
        assert MessageType.from_str("command") == MessageType.COMMAND
        assert MessageType.from_str("log_control") == MessageType.LOG_CONTROL
        assert MessageType.from_str("proactive") == MessageType.PROACTIVE
        assert MessageType.from_str("ambient") == MessageType.AMBIENT
        assert MessageType.from_str("system_notice") == MessageType.SYSTEM_NOTICE
        assert MessageType.from_str("system_log") == MessageType.SYSTEM_LOG

    def test_unknown_value_fallback_to_ambient(self):
        """未知类型回退为 AMBIENT，而非 CHAT"""
        result = MessageType.from_str("nonexistent")
        assert result == MessageType.AMBIENT
        assert result != MessageType.CHAT

    def test_empty_string_fallback(self):
        result = MessageType.from_str("")
        assert result == MessageType.AMBIENT
