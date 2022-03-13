from typing import Literal


class GroupInfo:
    def __init__(self, group_id: str):
        self.group_id: str = group_id
        self.group_name: str = ""
        self.member_count: int = 0
        self.max_member_count: int = 0


class GroupMemberInfo:
    def __init__(self, group_id: str, user_id: str):
        self.group_id: str = group_id
        self.user_id: str = user_id
        self.nickname: str = ""
        self.card: str = ""
        self.role: Literal["owner", "admin", "member"] = "member"
        self.title: str = ""
