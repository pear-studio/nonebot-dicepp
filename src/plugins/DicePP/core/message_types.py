"""消息类型分类法，用于统一消息表中的 type 列"""

from enum import StrEnum
from typing import Type, TypeVar

T = TypeVar("T", bound="MessageType")


class MessageType(StrEnum):
    CHAT = "chat"
    COMMAND = "command"
    SYSTEM_NOTICE = "system_notice"
    SYSTEM_LOG = "system_log"

    @classmethod
    def from_str(cls: Type[T], value: str) -> T:
        """从字符串解析 MessageType，不合法时返回 CHAT"""
        try:
            return cls(value)
        except ValueError:
            return cls.CHAT
