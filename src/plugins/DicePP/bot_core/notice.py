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


class FriendAddNoticeData(NoticeData):
    """好友添加"""

    def __init__(self, user_id):
        self.user_id: str = user_id


