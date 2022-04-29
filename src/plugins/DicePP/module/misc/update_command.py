import os
from typing import List, Tuple, Any, Literal

from core.bot import Bot
from core.command.const import *
from core.command import UserCommandBase, custom_user_command
from core.command import BotCommandBase, BotSendMsgCommand
from core.communication import MessageMetaData, PrivateMessagePort, GroupMessagePort
from core.config import PROJECT_PATH
from utils.dicegit import GitRepository

CFG_UPDATE_SOURCE = "update_source"
CFG_DAILY_CHECK = "update_daily_check"
LOC_UPDATE = "update"

# DicePP Source
dpp_github_path = "https://github.com/pear-studio/nonebot-dicepp.git"
dpp_gitee_path = "https://gitee.com/pear_studio/nonebot-dicepp.git"


@custom_user_command(readable_name="升级检查", priority=DPP_COMMAND_PRIORITY_DEFAULT)
class UpdateCommand(UserCommandBase):

    def __init__(self, bot: Bot):
        super().__init__(bot)
        bot.loc_helper.register_loc_text(LOC_UPDATE, "{TextArea}")  # ToDo: 没必要在这里设置自定义文本, 直接删掉吧

        bot.cfg_helper.register_config(CFG_UPDATE_SOURCE, "gitee", "可自定义的更新源, 目前支持gitee(推荐国内使用)与github.")
        bot.cfg_helper.register_config(CFG_DAILY_CHECK, "0", "每日更新检查, 1为检查, 0为不检查, 可以通过指令设置.")

        self.update_source: Literal["gitee", "github"] = "gitee"
        self.daily_check: bool = False

    def delay_init(self) -> List[str]:
        try:
            self.update_source = self.bot.cfg_helper.get_config(key=CFG_UPDATE_SOURCE)[0].lower()
            assert self.update_source in ["gitee", "github"]
        except (AssertionError, IndexError):
            self.update_source = "gitee"
        try:
            daily_check_val = int(self.bot.cfg_helper.get_config(key=CFG_DAILY_CHECK)[0])
            self.daily_check = (daily_check_val != 0)
        except (ValueError, IndexError):
            self.daily_check = False
        return []

    def can_process_msg(self, msg_str: str, meta: MessageMetaData) -> Tuple[bool, bool, Any]:
        master_list = self.bot.get_master_ids()
        should_proc: bool = False
        should_pass: bool = False
        if meta.user_id not in master_list:
            return should_proc, should_pass, None

        should_proc: bool = msg_str.startswith(".update")
        return should_proc, should_pass, msg_str[7:].strip()

    def process_msg(self, msg_str: str, meta: MessageMetaData, hint: Any) -> List[BotCommandBase]:
        port = GroupMessagePort(meta.group_id) if meta.group_id else PrivateMessagePort(meta.user_id)
        # 解析语句
        arg_str = hint

        if self.update_source.lower() == "github":
            git_path = dpp_github_path
        elif self.update_source.lower() == "gitee":
            git_path = dpp_gitee_path
        else:
            feedback = "初始化Git仓库失败, 请检查您的配置项是否正确。"
            return [BotSendMsgCommand(self.bot.account, feedback, [port])]
        try:
            git_repo = GitRepository(PROJECT_PATH, git_path, self.update_source)
        except AssertionError:
            raise NotImplementedError()  # ToDo

        if arg_str == "更新":
            textarea = git_repo.update()
            feedback: str = self.format_loc(LOC_UPDATE, TextArea=textarea)
        elif arg_str == "初始化":
            textarea = git_repo.refresh()
            feedback: str = self.format_loc(LOC_UPDATE, TextArea=textarea)
        elif not arg_str:
            textarea = git_repo.get_update()
            feedback: str = self.format_loc(LOC_UPDATE, TextArea=textarea)
        else:
            feedback = "请输入.help update查看指令说明"
        return [BotSendMsgCommand(self.bot.account, feedback, [port])]

    def tick_daily(self) -> List[BotCommandBase]:
        if self.update_source.lower() == "github":
            git_path = dpp_github_path
        elif self.update_source.lower() == "gitee":
            git_path = dpp_gitee_path
        else:
            self.bot.send_msg_to_master("初始化Git仓库失败, 请检查您的配置项是否正确。")  # ToDo: 错误用法
            return []
        try:
            gitrepo = GitRepository(PROJECT_PATH, git_path, self.update_source)
        except AssertionError:
            raise NotImplementedError()  # ToDo: 请处理异常
        textarea = gitrepo.get_update()
        textarea1 = gitrepo.is_dirty_check()
        feedback: str = self.format_loc(LOC_UPDATE, TextArea=textarea)
        feedback1: str = self.format_loc(LOC_UPDATE, TextArea=textarea1)
        if not textarea1:
            self.bot.send_msg_to_master(feedback)  # ToDo: 错误用法
            return []
        else:
            self.bot.send_msg_to_master(feedback)  # ToDo: 错误用法
            self.bot.send_msg_to_master(feedback1)  # ToDo: 错误用法
        return []

    def get_help(self, keyword: str, meta: MessageMetaData) -> str:
        if keyword == "update":  # help后的接着的内容
            feedback: str = ".update     # 手动检查更新\n" \
                            ".update 更新 # 下载并应用更新(需重启生效)\n" \
                            ".update 初始化 # 恢复被修改的源代码\n"
            return feedback
        return ""

    def get_description(self) -> str:
        return ".update 更新相关指令"  # help指令中返回的内容
