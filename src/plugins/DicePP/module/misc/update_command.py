import os
from typing import List, Tuple, Any

from core.bot import Bot
from core.command.const import *
from core.command import UserCommandBase, custom_user_command
from core.command import BotCommandBase, BotSendMsgCommand
from core.communication import MessageMetaData, PrivateMessagePort, GroupMessagePort
from utils.dicegit import GitRepository

LOC_UPDATE = "update"
DAILY_CHECK = True
dpp_path = os.path.abspath(".")

# DicePP Source
dpp_github_path = "https://github.com/pear-studio/nonebot-dicepp.git"
dpp_gitee_path = "https://gitee.com/pear_studio/nonebot-dicepp.git"
update_source = 'gitee'  # 更新源


@custom_user_command(readable_name="升级检查", priority=DPP_COMMAND_PRIORITY_DEFAULT)
class UpdateCommand(UserCommandBase):

    def __init__(self, bot: Bot):
        super().__init__(bot)
        bot.loc_helper.register_loc_text(LOC_UPDATE, "{TextArea}.", ".update返回的内容.{TextArea}文本请于dicegit寻找.")

    def can_process_msg(self, msg_str: str, meta: MessageMetaData) -> Tuple[bool, bool, Any]:
        should_proc: bool = msg_str.startswith(".update")
        should_pass: bool = False
        return should_proc, should_pass, msg_str[7:].strip()

    def process_msg(self, msg_str: str, meta: MessageMetaData, hint: Any) -> List[BotCommandBase]:
        port = GroupMessagePort(meta.group_id) if meta.group_id else PrivateMessagePort(meta.user_id)
        # 解析语句
        arg_str = hint
        TextArea: str = ''

        if update_source.lower() == "github":
            gitrepo = GitRepository(dpp_path, dpp_github_path, update_source)
        elif update_source.lower() == "gitee":
            gitrepo = GitRepository(dpp_path, dpp_gitee_path, update_source)
        else:
            raise "初始化失败,请检查您的配置项是否正确。"

        if arg_str.lower() == "merge":
            TextArea1 = gitrepo.update()
            feedback: str = self.format_loc(LOC_UPDATE, TextArea=TextArea)
            feedback2: str = self.format_loc(LOC_UPDATE, TextArea=TextArea1)
            return [BotSendMsgCommand(self.bot.account, feedback2, [port]), BotSendMsgCommand(self.bot.account, feedback, [port])]
        elif arg_str.lower() == 'resourcecode':
            TextArea = gitrepo.refresh()
            feedback: str = self.format_loc(LOC_UPDATE, TextArea=TextArea)
            return [BotSendMsgCommand(self.bot.account, feedback, [port])]
        elif arg_str.lower() == 'stopcheckupdate':
            global DAILY_CHECK
            DAILY_CHECK = False
        elif arg_str.lower() is None or arg_str.lower() == "":
            TextArea = gitrepo.get_update()
            feedback: str = self.format_loc(LOC_UPDATE, TextArea=TextArea)
            return [BotSendMsgCommand(self.bot.account, feedback, [port])]

    def tick_daily(self):
        global DAILY_CHECK
        if not DAILY_CHECK:
            return 0
        if update_source.lower() == "github":
            gitrepo = GitRepository(dpp_path, dpp_github_path, update_source)
        elif update_source.lower() == "gitee":
            gitrepo = GitRepository(dpp_path, dpp_gitee_path, update_source)
        else:
            raise "初始化失败,请检查您的配置项是否正确。"
        TextArea = gitrepo.get_update()
        feedback: str = self.format_loc(LOC_UPDATE, TextArea=TextArea)
        return Bot.send_msg_to_master(feedback)

    def get_help(self, keyword: str, meta: MessageMetaData) -> str:
        if keyword == "update":  # help后的接着的内容
            feedback: str = ".update 手动检查更新\n.update merge 更新\n.update resourcecode 恢复被修改的源代码\n .update stopcheckupdate 停止每日检查更新"
            return feedback
        return ""

    def get_description(self) -> str:
        return ".update 更新"  # help指令中返回的内容
