import abc


class NoticeData(metaclass=abc.ABCMeta):
    """通知信息"""
    pass


class GroupIncreaseNoticeData(NoticeData):
    """群成员增加"""

    def __init__(self, user_id, group_id, operator_id):
        self.user_id: str = user_id
        self.group_id: str = group_id
        self.operator_id: str = operator_id

    def __str__(self):
        return f"GroupIncrease: \033[0;37m{self.user_id}\033[0m join \033[0;37m{self.group_id}\033[0m (Op:\033[0;37m{self.operator_id}\033[0m)"


class FriendAddNoticeData(NoticeData):
    """好友添加"""

    def __init__(self, user_id):
        self.user_id: str = user_id

    def __str__(self):
        return f"FriendAdd: \033[0;37m{self.user_id}\033[0m"

