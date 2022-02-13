import abc
from typing import List


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


class BotCommandBase(metaclass=abc.ABCMeta):
    """
    所有机器人指令的基类, 包括发送消息, 退群等等
    """
    def __str__(self):
        return str(self.__class__.__name__)


class BotSendMsgCommand(BotCommandBase):
    """
    发送聊天消息
    """

    def __init__(self, bot_id: str, msg: str, targets: List[MessagePort]):
        self.bot_id = bot_id
        self.msg = msg
        self.targets = targets

    def __str__(self):
        s = f"Bot \033[0;37m{self.bot_id}\033[0m send message \033[0;33m{self.msg}\033[0m to "
        s += '\n\t'.join([str(target) for target in self.targets])
        return s


class BotDelayCommand(BotCommandBase):
    """
    延迟执行后面的操作
    """

    def __init__(self, bot_id: str, seconds: float):
        self.bot_id = bot_id
        self.seconds = seconds

    def __str__(self):
        s = f"Bot \033[0;37m{self.bot_id}\033[0m delay \033[0;33m{self.seconds}\033[0m seconds"
        return s


class BotLeaveGroupCommand(BotCommandBase):
    """
    退出群
    """

    def __init__(self, bot_id: str, target_group_id: str):
        self.bot_id = bot_id
        self.target_group_id = target_group_id

    def __str__(self):
        s = f"Bot \033[0;37m{self.bot_id}\033[0m leave group \033[0;33m{self.target_group_id}\033[0m"
        return s
