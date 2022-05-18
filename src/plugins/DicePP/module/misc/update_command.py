import os
from typing import List, Tuple, Any, Literal, Callable, Optional

from core.bot import Bot
from core.command.const import *
from core.command import UserCommandBase, custom_user_command
from core.command import BotCommandBase, BotSendMsgCommand
from core.communication import MessageMetaData, PrivateMessagePort, GroupMessagePort
from core.config import PROJECT_PATH
from utils.dicegit import GitRepository

BOT_ROOT_PATH = os.path.dirname(os.path.dirname(os.path.dirname(PROJECT_PATH)))
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
        bot.cfg_helper.register_config(CFG_UPDATE_SOURCE, "gitee", "可自定义的更新源, 目前支持gitee(推荐国内使用)与github.")
        bot.cfg_helper.register_config(CFG_DAILY_CHECK, "0", "每日更新检查, 1为检查, 0为不检查.")

        self.update_source: Literal["gitee", "github"] = "gitee"
        self.daily_check: bool = False
        self.git_repo = None
        self.master_list = []

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
        try:
            self.master_list = self.bot.get_master_ids()
        finally:
            pass
        return []

    def can_process_msg(self, msg_str: str, meta: MessageMetaData) -> Tuple[bool, bool, Any]:
        should_proc: bool = False
        should_pass: bool = False
        if meta.user_id not in self.master_list[0]:
            return should_proc, should_pass, None

        should_proc: bool = msg_str.startswith(".update")
        return should_proc, should_pass, msg_str[7:].strip()

    def process_msg(self, msg_str: str, meta: MessageMetaData, hint: Any) -> List[BotCommandBase]:
        port = GroupMessagePort(meta.group_id) if meta.group_id else PrivateMessagePort(meta.user_id)
        # 解析语句
        arg_str = hint
        feedback = ""

        if self.update_source.lower() == "github":
            git_path = dpp_github_path
        elif self.update_source.lower() == "gitee":
            git_path = dpp_gitee_path
        else:
            feedback = "初始化Git仓库失败, 请检查您的配置项是否正确。"
            return [BotSendMsgCommand(self.bot.account, feedback, [port])]
        try:
            self.git_repo = GitRepository(BOT_ROOT_PATH, git_path, self.update_source)
        except AssertionError:
            raise NotImplementedError("未能成功初始化git仓库。")

        if arg_str == "更新":
            self.bot.register_task(self.get_update, is_async=False, timeout=0, timeout_callback=self.lose)
        elif arg_str == "初始化":
            self.bot.register_task(self.refresh, is_async=False, timeout=0, timeout_callback=self.lose)
        elif not arg_str:
            self.bot.register_task(self.update, is_async=False, timeout=0, timeout_callback=self.lose)
        else:
            feedback: str = "请输入.help update查看指令说明"
        if feedback is None:
            return []
        return [BotSendMsgCommand(self.bot.account, feedback, [port])]

    def tick_daily(self) -> List[BotCommandBase]:
        if self.bot.cfg_helper.get_config(CFG_DAILY_CHECK) == 0:
            return []
        if self.update_source.lower() == "github":
            git_path = dpp_github_path
        elif self.update_source.lower() == "gitee":
            git_path = dpp_gitee_path
        else:
            feedback: str = "初始化Git仓库失败, 请检查您的配置项是否正确。"
            return [BotSendMsgCommand(self.bot.account, feedback, [PrivateMessagePort(self.master_list[0])])]
        try:
            self.git_repo = GitRepository(BOT_ROOT_PATH, git_path, self.update_source)
        except AssertionError:
            raise NotImplementedError("未能成功初始化git仓库。")
        self.bot.register_task(self.update, is_async=False, timeout=0, timeout_callback=self.lose)
        self.bot.register_task(self.is_dirty_check, is_async=False, timeout=0, timeout_callback=self.lose)
        return []

    def update(self) -> List[BotCommandBase]:
        return [BotSendMsgCommand(self.bot.account, self.git_repo.get_update(), [PrivateMessagePort(self.master_list[0])])]

    def get_update(self) -> List[BotCommandBase]:
        return [BotSendMsgCommand(self.bot.account, self.git_repo.update(), [PrivateMessagePort(self.master_list[0])])]

    def refresh(self) -> List[BotCommandBase]:
        return [BotSendMsgCommand(self.bot.account, self.git_repo.refresh(), [PrivateMessagePort(self.master_list[0])])]

    def is_dirty_check(self) -> List[BotCommandBase]:
        return [BotSendMsgCommand(self.bot.account, self.git_repo.is_dirty_check(), [PrivateMessagePort(self.master_list[0])])]

    def lose(self) -> List[BotCommandBase]:
        return [BotSendMsgCommand(self.bot.account, "失败.", [PrivateMessagePort(self.master_list[0])])]

    def get_help(self, keyword: str, meta: MessageMetaData) -> str:
        if keyword == "update":  # help后的接着的内容
            feedback: str = ".update     # 手动检查更新\n" \
                            ".update 更新 # 下载并应用更新(需重启生效)\n" \
                            ".update 初始化 # 恢复被修改的源代码\n"
            return feedback
        return ""

    def get_description(self) -> str:
        return ".update 更新相关指令"  # help指令中返回的内容
