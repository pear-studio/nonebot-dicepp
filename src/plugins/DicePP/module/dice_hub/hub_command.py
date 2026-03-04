from typing import List, Tuple, Any, Literal
import asyncio

from core.bot import Bot
from core.command.const import *
from core.command import UserCommandBase, custom_user_command
from core.command import BotCommandBase, BotSendMsgCommand
from core.communication import MessageMetaData, PrivateMessagePort, GroupMessagePort

from module.dice_hub.manager import HubManager
from module.dice_hub.api_client import HubAPIError
from utils.logger import dice_log


def run_async(coro):
    """在线程池中运行异步函数"""
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = None
    
    if loop and loop.is_running():
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(asyncio.run, coro)
            return future.result()
    else:
        return asyncio.run(coro)

LOC_HUB_REGISTER_SUCCESS = "hub_register_success"
LOC_HUB_REGISTER_ERROR = "hub_register_error"
LOC_HUB_NOT_CONFIGURED = "hub_not_configured"
LOC_HUB_KEY = "hub_key"
LOC_HUB_KEY_NOT_REGISTERED = "hub_key_not_registered"
LOC_HUB_LIST = "hub_list"
LOC_HUB_LIST_EMPTY = "hub_list_empty"
LOC_HUB_ONLINE_SUCCESS = "hub_online_success"
LOC_HUB_ONLINE_FAIL = "hub_online_fail"
LOC_HUB_ONLINE_NOT_REGISTERED = "hub_online_not_registered"
LOC_HUB_URL_SET = "hub_url_set"
LOC_HUB_URL_GET = "hub_url_get"


@custom_user_command(readable_name="DiceHub指令", priority=DPP_COMMAND_PRIORITY_DEFAULT,
                     flag=DPP_COMMAND_FLAG_HUB)
class HubCommand(UserCommandBase):
    def __init__(self, bot: Bot):
        super().__init__(bot)

        self.bot.loc_helper.register_loc_text(
            LOC_HUB_REGISTER_SUCCESS,
            "注册成功！API Key: {api_key}\n请在网站绑定此 Key 到您的账号",
            "注册成功时返回的消息",
        )
        self.bot.loc_helper.register_loc_text(
            LOC_HUB_REGISTER_ERROR,
            "注册失败: {error}",
            "注册失败时返回的消息",
        )
        self.bot.loc_helper.register_loc_text(
            LOC_HUB_NOT_CONFIGURED,
            "请先配置 DiceHub API 地址，使用 .hub url <地址>",
            "未配置 API 地址时的提示",
        )
        self.bot.loc_helper.register_loc_text(
            LOC_HUB_KEY,
            "当前机器人 API Key: {api_key}",
            "查看 API Key 时的回复",
        )
        self.bot.loc_helper.register_loc_text(
            LOC_HUB_KEY_NOT_REGISTERED,
            "机器人尚未注册，请先使用 .hub register 注册",
            "未注册时查看 API Key 的提示",
        )
        self.bot.loc_helper.register_loc_text(
            LOC_HUB_LIST,
            "在线机器人列表:\n{list}",
            "查看在线列表时的回复",
        )
        self.bot.loc_helper.register_loc_text(
            LOC_HUB_LIST_EMPTY,
            "暂无在线机器人",
            "在线列表为空时的回复",
        )
        self.bot.loc_helper.register_loc_text(
            LOC_HUB_ONLINE_SUCCESS,
            "心跳发送成功",
            "心跳成功时的回复",
        )
        self.bot.loc_helper.register_loc_text(
            LOC_HUB_ONLINE_FAIL,
            "心跳发送失败: {error}",
            "心跳失败时的回复",
        )
        self.bot.loc_helper.register_loc_text(
            LOC_HUB_ONLINE_NOT_REGISTERED,
            "机器人尚未注册，请先使用 .hub register 注册",
            "未注册时发送心跳的提示",
        )
        self.bot.loc_helper.register_loc_text(
            LOC_HUB_URL_SET,
            "DiceHub API 地址已设置为: {url}",
            "设置 URL 成功时的回复",
        )
        self.bot.loc_helper.register_loc_text(
            LOC_HUB_URL_GET,
            "当前 DiceHub API 地址: {url}",
            "查看 URL 时的回复",
        )

        from core.config import CFG_HUB_ENABLE
        self.bot.cfg_helper.register_config(CFG_HUB_ENABLE, "1", "1为开启, 0为关闭")

    def can_process_msg(self, msg_str: str, meta: MessageMetaData) -> Tuple[bool, bool, Any]:
        should_proc: bool = False
        should_pass: bool = False
        hint = None

        try:
            from core.config import CFG_HUB_ENABLE
            assert int(self.bot.cfg_helper.get_config(CFG_HUB_ENABLE)[0]) == 1
        except (AssertionError, ValueError, IndexError):
            return should_proc, should_pass, hint

        if msg_str.startswith(".hub"):
            msg_str = msg_str[5:].strip()
            if msg_str:
                hint = msg_str
                should_proc = True

        return should_proc, should_pass, hint

    def process_msg(self, msg_str: str, meta: MessageMetaData, hint: Any) -> List[BotCommandBase]:
        from core.config import CFG_MASTER
        if meta.user_id not in self.bot.cfg_helper.get_config(CFG_MASTER):
            return []

        port = GroupMessagePort(meta.group_id) if meta.group_id else PrivateMessagePort(meta.user_id)

        command = hint.strip()
        command_list = []

        if not command:
            command_list.append(BotSendMsgCommand(
                self.bot.account,
                "DiceHub 指令:\n"
                "- .hub register: 注册机器人\n"
                "- .hub key: 查看 API Key\n"
                "- .hub list: 查看在线列表\n"
                "- .hub online: 手动发送心跳\n"
                "- .hub url <地址>: 设置/查看 API 地址",
                [port],
            ))
            return command_list

        parts = command.split(maxsplit=1)
        action = parts[0]
        arg = parts[1] if len(parts) > 1 else ""

        if action == "register":
            command_list += self._handle_register(port)
        elif action == "key":
            command_list += self._handle_key(port)
        elif action == "list":
            command_list += self._handle_list(port)
        elif action == "online":
            command_list += self._handle_online(port)
        elif action == "url":
            command_list += self._handle_url(arg, port)
        elif action == "status":
            command_list += self._handle_status(port)
        else:
            command_list.append(BotSendMsgCommand(
                self.bot.account,
                f"未知指令: {action}",
                [port],
            ))

        return command_list

    def _handle_register(self, port) -> List[BotCommandBase]:
        command_list = []

        dice_log(f"[DiceHub] 收到注册请求, is_configured={self.bot.hub_manager.is_configured()}")

        if not self.bot.hub_manager.is_configured():
            dice_log(f"[DiceHub] 注册失败: 未配置 API 地址")
            return [BotSendMsgCommand(
                self.bot.account,
                self.format_loc(LOC_HUB_NOT_CONFIGURED),
                [port],
            )]

        async def do_register():
            try:
                dice_log(f"[DiceHub] 开始注册机器人...")
                result = await self.bot.hub_manager.register()
                api_key = result.get("api_key", "")
                dice_log(f"[DiceHub] 注册成功, api_key={api_key[:8]}...")
                return self.format_loc(LOC_HUB_REGISTER_SUCCESS, api_key=api_key)
            except HubAPIError as e:
                dice_log(f"[DiceHub] 注册失败 (API错误): {e}")
                return self.format_loc(LOC_HUB_REGISTER_ERROR, error=str(e))
            except Exception as e:
                dice_log(f"[DiceHub] 注册失败 (异常): {e}")
                return self.format_loc(LOC_HUB_REGISTER_ERROR, error=str(e))

        feedback = run_async(do_register())
        command_list.append(BotSendMsgCommand(self.bot.account, feedback, [port]))
        return command_list

    def _handle_key(self, port) -> List[BotCommandBase]:
        dice_log(f"[DiceHub] 收到查看 Key 请求, is_registered={self.bot.hub_manager.is_registered()}")
        if not self.bot.hub_manager.is_registered():
            return [BotSendMsgCommand(
                self.bot.account,
                self.format_loc(LOC_HUB_KEY_NOT_REGISTERED),
                [port],
            )]

        api_key = self.bot.hub_manager.get_api_key()
        dice_log(f"[DiceHub] 返回 API Key: {api_key[:8]}...")
        return [BotSendMsgCommand(
            self.bot.account,
            self.format_loc(LOC_HUB_KEY, api_key=api_key),
            [port],
        )]

    def _handle_list(self, port) -> List[BotCommandBase]:
        dice_log(f"[DiceHub] 收到查看列表请求, is_registered={self.bot.hub_manager.is_registered()}")
        if not self.bot.hub_manager.is_registered():
            return [BotSendMsgCommand(
                self.bot.account,
                self.format_loc(LOC_HUB_KEY_NOT_REGISTERED),
                [port],
            )]

        async def do_list():
            try:
                robots = await self.bot.hub_manager.get_online_robots()
                dice_log(f"[DiceHub] 获取到 {len(robots)} 个在线机器人")
                if not robots:
                    return self.format_loc(LOC_HUB_LIST_EMPTY)
                return self.format_loc(LOC_HUB_LIST, list=self.bot.hub_manager.generate_list_message())
            except Exception as e:
                dice_log(f"[DiceHub] 获取列表失败: {e}")
                return f"获取列表失败: {str(e)}"

        feedback = run_async(do_list())
        return [BotSendMsgCommand(self.bot.account, feedback, [port])]

    def _handle_online(self, port) -> List[BotCommandBase]:
        dice_log(f"[DiceHub] 收到心跳请求, is_registered={self.bot.hub_manager.is_registered()}")
        if not self.bot.hub_manager.is_registered():
            return [BotSendMsgCommand(
                self.bot.account,
                self.format_loc(LOC_HUB_ONLINE_NOT_REGISTERED),
                [port],
            )]

        async def do_heartbeat():
            try:
                success = await self.bot.hub_manager.heartbeat()
                if success:
                    dice_log(f"[DiceHub] 手动心跳成功")
                    return self.format_loc(LOC_HUB_ONLINE_SUCCESS)
                else:
                    dice_log(f"[DiceHub] 手动心跳失败")
                    return self.format_loc(LOC_HUB_ONLINE_FAIL, error="心跳请求失败")
            except Exception as e:
                return self.format_loc(LOC_HUB_ONLINE_FAIL, error=str(e))

        feedback = run_async(do_heartbeat())
        return [BotSendMsgCommand(self.bot.account, feedback, [port])]

    def _handle_url(self, arg: str, port) -> List[BotCommandBase]:
        from core.config import CFG_HUB_API_URL
        from core.config.config_item import ConfigItem

        if arg:
            self.bot.cfg_helper.all_configs[CFG_HUB_API_URL] = ConfigItem(
                CFG_HUB_API_URL, arg.strip()
            )
            self.bot.cfg_helper.save_config()
            return [BotSendMsgCommand(
                self.bot.account,
                self.format_loc(LOC_HUB_URL_SET, url=arg.strip()),
                [port],
            )]
        else:
            url = self.bot.hub_manager.get_api_url()
            if not url:
                url = "未设置"
            return [BotSendMsgCommand(
                self.bot.account,
                self.format_loc(LOC_HUB_URL_GET, url=url),
                [port],
            )]

    def _handle_status(self, port) -> List[BotCommandBase]:
        status_msg = self.bot.hub_manager.generate_status_message()
        return [BotSendMsgCommand(self.bot.account, status_msg, [port])]

    def tick(self) -> List[BotCommandBase]:
        self.bot.hub_manager.tick()
        return []

    def get_help(self, keyword: str, meta: MessageMetaData) -> str:
        return "DiceHub 指令帮助:\n" \
               ".hub register - 注册机器人\n" \
               ".hub key - 查看 API Key\n" \
               ".hub list - 查看在线列表\n" \
               ".hub online - 手动发送心跳\n" \
               ".hub url <地址> - 设置/查看 API 地址"

    def get_description(self) -> str:
        return "DiceHub 多机器人互联"
