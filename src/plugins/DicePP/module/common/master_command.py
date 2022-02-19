"""
命令模板, 复制到新创建的文件里修改
"""

from typing import List, Tuple, Any

from core.bot import Bot
from core.command.const import *
from core.command import UserCommandBase, custom_user_command
from core.command import BotCommandBase, BotSendMsgCommand
from core.communication import MessageMetaData, PrivateMessagePort, GroupMessagePort
from core.config import CFG_MASTER

LOC_REBOOT = "master_reboot"
LOC_SEND_MASTER = "master_send_to_master"
LOC_SEND_TARGET = "master_send_to_target"


@custom_user_command(readable_name="Master指令", priority=DPP_COMMAND_PRIORITY_MASTER)
class MasterCommand(UserCommandBase):
    """
    Master指令
    包括: reboot, send
    """

    def __init__(self, bot: Bot):
        super().__init__(bot)
        bot.loc_helper.register_loc_text(LOC_REBOOT, "Reboot Complete", "重启完成")
        bot.loc_helper.register_loc_text(LOC_SEND_MASTER,
                                         "Send message: {msg} to {id} (type:{type})",
                                         "用.m send指令发送消息时给Master的回复")
        bot.loc_helper.register_loc_text(LOC_SEND_TARGET, "From Master: {msg}", "用.m send指令发送消息时给目标的回复")

    def can_process_msg(self, msg_str: str, meta: MessageMetaData) -> Tuple[bool, bool, Any]:
        master_list = self.bot.cfg_helper.get_config(CFG_MASTER)
        should_proc: bool = False
        should_pass: bool = False
        if meta.user_id not in master_list:
            return should_proc, should_pass, None

        should_proc = msg_str.startswith(".m")
        return should_proc, should_pass, msg_str[2:].strip()

    def process_msg(self, msg_str: str, meta: MessageMetaData, hint: Any) -> List[BotCommandBase]:
        port = GroupMessagePort(meta.group_id) if meta.group_id else PrivateMessagePort(meta.user_id)
        # 解析语句
        arg_str: str = hint
        feedback: str
        command_list: List[BotCommandBase] = []

        if arg_str == "reboot":
            # noinspection PyBroadException
            try:
                self.bot.reboot()
                feedback = self.format_loc(LOC_REBOOT)
            except Exception:
                return self.bot.handle_exception("重启时出现错误")
        elif arg_str.startswith("send"):
            arg_list = arg_str[4:].split(":", 2)
            if len(arg_list) == 3:
                target_type, target, msg = (arg.strip() for arg in arg_list)
                if target_type in ["user", "group"]:
                    feedback = self.format_loc(LOC_SEND_MASTER, msg=msg, id=target, type=target_type)
                    target_port = PrivateMessagePort(target) if target_type == "user" else GroupMessagePort(target)
                    command_list.append(BotSendMsgCommand(self.bot.account, msg, [target_port]))
                else:
                    feedback = "目标必须为user或group"
            else:
                feedback = f"非法输入\n使用方法: {self.get_help('m send', meta)}"
        else:
            feedback = self.get_help("m", meta)

        command_list.append(BotSendMsgCommand(self.bot.account, feedback, [port]))
        return command_list

    def get_help(self, keyword: str, meta: MessageMetaData) -> str:
        if keyword == "m":  # help后的接着的内容
            return ".m reboot 重启骰娘" \
                   ".m send 命令骰娘发送信息"
        if keyword.startswith("m"):
            if keyword.endswith("reboot"):
                return "该指令只是保存后重新加载一遍本地文件, 不能替代重启DicePP等操作"
            elif keyword.endswith("send"):
                return ".m send [user/group]:[账号/群号]:[消息内容]"
        return ""

    def get_description(self) -> str:
        return ".m Master才能使用的指令"  # help指令中返回的内容
