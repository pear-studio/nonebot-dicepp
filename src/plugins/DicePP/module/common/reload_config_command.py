"""
.reload command — hot-reload all configuration and persona without restarting the Bot.
Only admin (permission >= 3) or master (permission == 4) may use this command.
"""
import datetime
from typing import Any, List, Tuple

from core.bot import Bot
from core.command import UserCommandBase, custom_user_command
from core.command import BotCommandBase, BotSendMsgCommand
from core.command.const import DPP_COMMAND_PRIORITY_DEFAULT, DPP_COMMAND_FLAG_MANAGE
from core.communication import MessageMetaData, PrivateMessagePort, GroupMessagePort
from core.config.loader import ConfigValidationError
from utils.logger import dice_log

LOC_RELOAD_OK = "reload_ok"
LOC_RELOAD_FAIL = "reload_fail"


@custom_user_command(
    readable_name="配置热重载指令",
    priority=DPP_COMMAND_PRIORITY_DEFAULT,
    flag=DPP_COMMAND_FLAG_MANAGE,
    permission_require=3,  # admin or master
)
class ReloadConfigCommand(UserCommandBase):
    """Handles the .reload command for hot configuration reloading."""

    def __init__(self, bot: Bot):
        super().__init__(bot)
        bot.loc_helper.register_loc_text(
            LOC_RELOAD_OK,
            "配置已重新加载。({timestamp})",
            ".reload 成功时的回复，{timestamp} 为时间戳",
        )
        bot.loc_helper.register_loc_text(
            LOC_RELOAD_FAIL,
            "配置重载失败，已保留旧配置。\n错误详情：{error}",
            ".reload 失败时的回复，{error} 为错误详情",
        )

    def can_process_msg(self, msg_str: str, meta: MessageMetaData) -> Tuple[bool, bool, Any]:
        should_proc = msg_str.strip() == ".reload"
        return should_proc, False, None

    async def process_msg(self, msg_str: str, meta: MessageMetaData, hint: Any) -> List[BotCommandBase]:
        port = GroupMessagePort(meta.group_id) if meta.group_id else PrivateMessagePort(meta.user_id)
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        dice_log(f"[Reload] User {meta.user_id} triggered .reload at {timestamp}")

        try:
            # Atomically reload config (raises ConfigValidationError on failure)
            new_config = self.bot._cfg_loader.reload()
            self.bot.config = new_config

            # Reload persona data
            self.bot._persona_loader.reload()

            # Re-apply persona overrides to localization
            self.bot.loc_helper.reset_to_default()
            self.bot.loc_helper.set_persona(new_config.persona)

            dice_log(f"[Reload] Config reloaded successfully by {meta.user_id}")
            feedback = self.bot.loc_helper.format_loc_text(LOC_RELOAD_OK, timestamp=timestamp)
        except ConfigValidationError as exc:
            error_msg = str(exc)
            dice_log(f"[Reload] Config reload FAILED by {meta.user_id}: {error_msg}")
            feedback = self.bot.loc_helper.format_loc_text(LOC_RELOAD_FAIL, error=error_msg)
        except Exception as exc:
            error_msg = f"{type(exc).__name__}: {exc}"
            dice_log(f"[Reload] Config reload FAILED by {meta.user_id}: {error_msg}")
            feedback = self.bot.loc_helper.format_loc_text(LOC_RELOAD_FAIL, error=error_msg)

        return [BotSendMsgCommand(self.bot.account, feedback, [port])]

    def get_help(self, keyword: str, meta: MessageMetaData) -> str:
        return ""

    def get_description(self) -> str:
        return ".reload 热重载配置文件（需骰管理权限）"
