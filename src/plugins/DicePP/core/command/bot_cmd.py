import abc
import re
from typing import List

from core.communication import MessagePort

from nonebot.adapters.onebot.v11 import Message as CQMessage


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
        def handle_base64img(match):  # 如果是base64编码就不要显示了
            return "[CQ:image,file=base64:...]"

        processed_msg = re.sub(r"\[CQ:image,file=base64:.*]", handle_base64img, self.msg)
        s = f"Bot \033[0;37m{self.bot_id}\033[0m send message \033[0;33m{processed_msg}\033[0m to "
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
    退出群（群内限定）
    """

    def __init__(self, bot_id: str, target_group_id: str):
        self.bot_id = bot_id
        self.target_group_id = target_group_id

    def __str__(self):
        s = f"Bot \033[0;37m{self.bot_id}\033[0m leave group \033[0;33m{self.target_group_id}\033[0m"
        return s

class BotSendForwardMsgCommand(BotCommandBase):
    """
    合并转发消息（群内限定）
    """

    def __init__(self, bot_id: str, name: str,msg: List[str], targets: List[MessagePort]):
        self.bot_id = bot_id
        self.msg = msg
        self.name = name
        self.targets = targets
        self.msg_json_list = []
        for sub_msg in msg:
            self.msg_json_list.append({"type": "node","data": {"name": name,"uin": int(bot_id),"content": CQMessage(sub_msg)}})

    def __str__(self):
        def handle_base64img(match):  # 如果是base64编码就不要显示了
            return "[CQ:image,file=base64:...]"

        processed_msg = re.sub(r"\[CQ:image,file=base64:.*]", handle_base64img, "\n".join([("|" + msg.splitlines()[0] + "...") for msg in self.msg]))
        s = f"Bot \033[0;37m{self.bot_id}\033[0m send message \033[0;33m{processed_msg}\033[0m to "
        s += '\n\t'.join([str(target) for target in self.targets])
        return s

class BotSendFileCommand(BotCommandBase):
    """
    上传文件（群内限定）
    """

    def __init__(self, bot_id: str, file: str, display_name: str, targets: List[MessagePort]):
        self.bot_id = bot_id
        self.file = file
        self.display_name = display_name
        self.targets = targets

    def __str__(self):
        s = f"Bot \033[0;37m{self.bot_id}\033[0m 发送文件 \033[0;33m{self.display_name}（{self.file}）\033[0m to "
        s += '\n\t'.join([str(target) for target in self.targets])
        return s
        
