import abc
from typing import List, Tuple, Dict, Type, Any

from core.bot import Bot
from core.communication import MessageMetaData

from core.command.const import *
from core.command.bot_cmd import BotCommandBase


class UserCommandBase(metaclass=abc.ABCMeta):
    """
    所有用户指令的基类
    """
    readable_name: str = "未命名指令"
    priority: int = DPP_COMMAND_PRIORITY_DEFAULT
    group_only: bool = False
    flag: int = DPP_COMMAND_FLAG_DEFAULT
    cluster: int = DPP_COMMAND_CLUSTER_DEFAULT

    def __init__(self, bot: Bot):
        """
        Args:
            bot: 所属的Bot实例
        """
        self.bot = bot
        self.format_loc = self.bot.loc_helper.format_loc_text  # 精简代码长度

    def delay_init(self) -> List[str]:
        """在机器人完成初始化后调用, 此时可以读取本地化文本和配置, 返回提示信息"""
        return []

    def tick(self) -> List[BotCommandBase]:
        """每秒调用一次的方法"""
        return []

    def tick_daily(self) -> List[BotCommandBase]:
        """每天调用一次"""
        return []

    @abc.abstractmethod
    def can_process_msg(self, msg_str: str, meta: MessageMetaData) -> Tuple[bool, bool, Any]:
        """
        确定一条信息能否被这个Command处理
        Args:
            msg_str: 预处理后的信息字符串
            meta: 消息的元信息, 包括原始信息字符串, 发送者id, bot id等等
        Returns:
            should_proc: 是否可以被处理
            should_pass: 如果可以被处理, 是否继续让该消息给其他命令处理. 若should_proc为False, 则该返回值不会被用到
            hint: 传给process_msg的提示
        """
        should_proc: bool = False
        should_pass: bool = False
        return should_proc, should_pass, None

    @abc.abstractmethod
    def process_msg(self, msg_str: str, meta: MessageMetaData, hint: Any) -> List[BotCommandBase]:
        """
        处理信息的函数
        Args:
            msg_str: 预处理后的信息字符串
            meta: 消息的元信息, 包括原始信息字符串, 发送者id, bot id等等
            hint: 预处理时给出的提示
        Returns:
            bot_commands: 一个bot commands list, 即bot要进行的操作, 比如回复消息等等
        """
        bot_commands = []
        return bot_commands

    @abc.abstractmethod
    def get_help(self, keyword: str, meta: MessageMetaData) -> str:
        """
        返回命令的使用帮助
        Args:
            keyword: 查询关键词
            meta: 消息的元信息, 包括原始信息字符串, 发送者id, bot id等等

        Returns:
            help_str: 帮助字符串, 如果关键词不符合预期, 应该返回空字符串
        """
        return ""

    @abc.abstractmethod
    def get_description(self) -> str:
        """
        返回命令的简短描述, 尽量不要超过一行
        """
        return ""


def custom_user_command(readable_name: str,
                        priority: int = DPP_COMMAND_PRIORITY_DEFAULT,
                        group_only: bool = False,
                        flag: int = DPP_COMMAND_FLAG_DEFAULT,
                        cluster: int = DPP_COMMAND_CLUSTER_DEFAULT):
    """
    装饰Command类, 给自定义的Command附加一些参数
    Args:
        readable_name: 可读的名称, 应当为中文
        priority: 优先级, 优先级高的类会先处理指令, 数字越小优先级越高
        group_only: 是否只能在群内使用, 如果为True且在私聊中捕获了对应消息, 则会返回提示
        flag: 标志位, 标志着指令的类型是DND指令, 娱乐指令等等, 主要用于profiler
        cluster: 所属的命令群组, 被用来开关某一组功能
    """

    def custom_inner(cls):
        """
        Args:
            cls: 要修饰的类, 必须继承自UserCommandBase
        """
        assert issubclass(cls, UserCommandBase)
        cls.readable_name = readable_name
        cls.priority = priority
        cls.group_only = group_only
        cls.flag = flag
        cls.cluster = cluster
        USER_COMMAND_CLS_DICT[cls.__name__] = cls
        return cls

    return custom_inner


class CommandError(Exception):
    """
    执行命令时产生的异常, 说明操作失败的原因, 应当Command内部使用, 不应该抛给Bot
    """

    def __init__(self, info: str, to_user: bool = False, to_master: bool = True):
        """

        Args:
            info: 消息内容
            to_user: 是否发送给用户
            to_master: 是否发送给管理员
        """
        self.info = info
        self.to_user = to_user
        self.to_master = to_master

    def __str__(self):
        return f"[Command] [Error] {self.info}"


USER_COMMAND_CLS_DICT: Dict[str, Type[UserCommandBase]] = {}
