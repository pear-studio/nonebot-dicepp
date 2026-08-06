"""Compatibility tombstone for the retired general ``.reload`` command."""
from typing import Any, List, Tuple

from plugins.DicePP.core.bot import Bot
from plugins.DicePP.core.command import UserCommandBase, custom_user_command
from plugins.DicePP.core.command import BotCommandBase, BotSendMsgCommand
from plugins.DicePP.core.command.const import DPP_COMMAND_FLAG_MANAGE
from plugins.DicePP.core.communication import MessageMetaData, PrivateMessagePort, GroupMessagePort
from plugins.DicePP.utils.logger import logger

LOC_RELOAD_DISABLED = "reload_disabled"


@custom_user_command(
    readable_name="配置重载停用提示",
    # ``.r`` accepts a broad prefix, so the exact legacy tombstone must route
    # before RollDiceCommand (priority 0).
    priority=-1,
    flag=DPP_COMMAND_FLAG_MANAGE,
    permission_require=3,  # admin or master
)
class ReloadConfigCommand(UserCommandBase):
    """Recognize legacy usage without changing any live Bot state."""

    def __init__(self, bot: Bot):
        super().__init__(bot)
        bot.loc_helper.register_loc_text(
            LOC_RELOAD_DISABLED,
            "通用配置热重载已停用，请在 Dashboard 重启 Bot RuntimeUnit 使配置生效。",
            ".reload 兼容提示；该命令不会修改运行中的配置",
        )

    def can_process_msg(self, msg_str: str, meta: MessageMetaData) -> Tuple[bool, bool, Any]:
        should_proc = msg_str.strip() == ".reload"
        return should_proc, False, None

    async def process_msg(self, msg_str: str, meta: MessageMetaData, hint: Any) -> List[BotCommandBase]:
        port = GroupMessagePort(meta.group_id) if meta.group_id else PrivateMessagePort(meta.user_id)
        logger.info(f"[Reload] User {meta.user_id} invoked retired .reload command")
        feedback = self.bot.loc_helper.format_loc_text(LOC_RELOAD_DISABLED)

        return [BotSendMsgCommand(self.bot.account, feedback, [port])]

    def get_help(self, keyword: str, meta: MessageMetaData) -> str:
        return ""

    def get_description(self) -> str:
        return ".reload 通用配置热重载已停用；请重启 Bot RuntimeUnit（需骰管理权限）"
