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
    """
    包含了一条消息的元信息
    """

    def __init__(self, plain_msg: str, raw_msg: str, sender: MessageSender, group_id: str = "", to_me: bool = False):
        self.plain_msg: str = plain_msg
        self.raw_msg: str = raw_msg
        self.sender: MessageSender = sender
        self.user_id: str = sender.user_id
        self.nickname: str = sender.nickname
        self.group_id: str = group_id
        self.to_me: bool = to_me

