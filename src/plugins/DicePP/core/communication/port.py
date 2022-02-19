class MessagePort:
    def __init__(self, group_id: str, user_id: str):
        """

        Args:
            group_id: 目标群号, 为空则代表私聊
            user_id: 目标账号, 不为空则代表私聊, group和user同时存在则只看user
        """
        self.group_id = group_id
        self.user_id = user_id

    def __str__(self):
        if not self.user_id and self.group_id:
            return f"\033[0;34m|Group: {self.group_id}|\033[0m"
        elif self.user_id:
            return f"\033[0;35m|Private: {self.user_id}|\033[0m"
        else:
            return f"\033[0;31m|Empty Message Target!|\033[0m"

    def __eq__(self, other):
        return self.__dict__ == other.__dict__

    def __hash__(self):
        return hash(self.group_id+self.user_id)


class PrivateMessagePort(MessagePort):
    def __init__(self, user_id: str):
        """

        Args:
            user_id: 目标账号
        """
        super().__init__("", user_id)


class GroupMessagePort(MessagePort):
    def __init__(self, group_id: str):
        """

        Args:
            group_id: 目标群号
        """
        super().__init__(group_id, "")