from typing import Optional

class MessageSender:
    def __init__(self, user_id: str, nickname: str):
        self.user_id: Optional[str] = user_id
        self.nickname: Optional[str] = nickname
        self.sex: Optional[str] = None
        self.age: Optional[int] = None
        self.card: Optional[str] = None
        self.area: Optional[str] = None
        self.level: Optional[str] = None
        self.role: Optional[str] = None
        self.title: Optional[str] = None


class MessageMetaData:
    """包含一条消息的元信息。

    追加: message_id (可选) 用于支持后续基于撤回事件的日志删除。
    某些上层适配器若无法提供 message_id 可保持 None。
    """

    def __init__(self, plain_msg: str, raw_msg: str, sender: MessageSender, group_id: str = "", to_me: bool = False):
        self.plain_msg: str = plain_msg
        self.raw_msg: str = raw_msg
        self.sender: MessageSender = sender
        self.user_id: str = sender.user_id
        self.nickname: str = sender.nickname
        self.group_id: str = group_id
        self.to_me: bool = to_me
        self.permission: int = 0
        # 新增字段：消息唯一 ID（OneBot v11 为 int，统一转为 str 存）
        self.message_id: Optional[str] = None

