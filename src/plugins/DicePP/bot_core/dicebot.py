import os
import asyncio
from typing import List, Optional, Dict

import bot_config
from bot_core import MessageMetaData, NoticeData, RequestData
from bot_core import FriendRequestData, JoinGroupRequestData, InviteGroupRequestData
from bot_core import FriendAddNoticeData, GroupIncreaseNoticeData
from data_manager import DataManager, DataManagerError, custom_data_chunk, DataChunkBase
import localization
from localization import LocalizationHelper
from bot_config import ConfigHelper
from logger import dice_log, get_exception_info


@custom_data_chunk(identifier="nickname")
class _(DataChunkBase):
    def __init__(self):
        super().__init__()


# noinspection PyBroadException
class Bot:
    def __init__(self, account: str):
        """
        实例化机器人
        Args:
            account: QQ账号
        """
        import command  # Command中可能会定义新的DataChunk和local text等, 所以要在之前import
        from adapter import ClientProxy
        self.account: str = account
        self.proxy: Optional[ClientProxy] = None
        self.data_path = os.path.join(bot_config.BOT_DATA_PATH, account)

        self.data_manager = DataManager(self.data_path)
        self.loc_helper = LocalizationHelper(bot_config.CONFIG_PATH, self.account)
        self.cfg_helper = ConfigHelper(bot_config.CONFIG_PATH, self.account)

        self.command_dict: Dict[str, command.UserCommandBase] = {}

        self.start_up()

    def start_up(self):
        self.register_command()
        self.loc_helper.load_localization()  # 要在注册完命令后再读取本地化文件
        self.loc_helper.save_localization()  # 更新本地文件
        self.cfg_helper.load_config()
        self.cfg_helper.save_config()

    def set_client_proxy(self, proxy):
        from adapter import ClientProxy
        if isinstance(proxy, ClientProxy):
            self.proxy = proxy
        else:
            raise TypeError("Incorrect Client Proxy!")

    def shutdown(self):
        """销毁bot对象时触发, 可能是bot断连, 或关闭应用导致的"""
        asyncio.create_task(self.shutdown_async())

    def shutdown_debug(self):
        """在载入本地化文本和配置等数据后调用, 必须是同步环境下调用"""
        asyncio.run(self.shutdown_async())

    async def shutdown_async(self):
        """
        shutdown的异步版本
        销毁bot对象时触发, 可能是bot断连, 或关闭应用导致的
        """
        await self.data_manager.save_data_async()
        # 注意如果保存时文件不存在会用当前值写入default, 如果在读取自定义设置后删掉文件再保存, 就会得到一个不是默认的default sheet
        # self.loc_helper.save_localization() # 暂时不会在运行时修改, 不需要保存
        # self.cfg_helper.save_config() # 暂时不会在运行时修改, 不需要保存

    def reboot(self):
        """重启bot"""
        asyncio.create_task(self.reboot_async())

    async def reboot_async(self):
        dice_log("[Bot] [Reboot] 开始重启")
        await self.shutdown_async()
        self.start_up()
        await self.delay_init_command()

    def register_command(self):
        import command
        command_cls_dict = command.dicepp_command.USER_COMMAND_CLS_DICT
        command_names = command_cls_dict.keys()
        command_names = sorted(command_names, key=lambda n: command_cls_dict[n].priority)  # 按优先级排序
        for command_name in command_names:
            command_cls = command_cls_dict[command_name]
            self.command_dict[command_name] = command_cls(bot=self)  # 默认的Dict是有序的, 所以之后用values拿到的也是有序的

    def delay_init(self):
        """在载入本地化文本和配置等数据后调用"""
        asyncio.create_task(self.delay_init_command())

    def delay_init_debug(self):
        """在载入本地化文本和配置等数据后调用, 必须是同步环境下调用"""
        loop = asyncio.get_event_loop()
        loop.run_until_complete(self.delay_init_command())

    async def delay_init_command(self):
        """在载入本地化文本和配置等数据后调用"""
        init_info: List[str] = []
        for command in self.command_dict.values():
            try:
                init_info_cur = command.delay_init()
                for i in range(len(init_info_cur)):
                    init_info_cur[i] = f"{command.__class__.readable_name}: {init_info_cur[i]}"
                init_info += init_info_cur
            except Exception:
                if self.proxy:
                    bc_list = self.handle_exception(f"加载{command.__class__.__name__}失败")  # 报错不用中文名
                    for bc in bc_list:
                        await self.proxy.process_bot_command(bc)
        if self.proxy:
            from command import PrivateMessagePort, BotSendMsgCommand
            feedback = "\n".join(["初始化完成!"] + init_info + ["准备好开始工作啦~"])
            from bot_config import CFG_MASTER
            dice_log(feedback)
            master_list = self.cfg_helper.get_config(CFG_MASTER)
            for master in master_list:  # 给Master汇报
                command = BotSendMsgCommand(self.account, feedback, [PrivateMessagePort(master)])
                await self.proxy.process_bot_command(command)

    # noinspection PyBroadException
    async def process_message(self, msg: str, meta: MessageMetaData) -> List:
        from command import preprocess_msg
        from command import PrivateMessagePort, BotCommandBase, BotSendMsgCommand

        self.update_nickname(meta.user_id, "origin", meta.nickname)

        msg = preprocess_msg(msg)  # 转换中文符号, 转换小写等等

        bot_commands: List[BotCommandBase] = []

        for command in self.command_dict.values():
            try:
                should_proc, should_pass, hint = command.can_process_msg(msg, meta)
            except Exception:
                # 发现未处理的错误, 汇报给主Master
                should_proc, should_pass, hint = False, False, None
                bot_commands += self.handle_exception(f"来源:{msg} 用户:{meta.user_id} 群:{meta.group_id} CODE100")
            if should_proc:
                if command.group_only and not meta.group_id:
                    # 在非群聊中企图执行群聊指令, 回复一条提示
                    feedback = self.loc_helper.format_loc_text(localization.LOC_GROUP_ONLY_NOTICE)
                    bot_commands += [BotSendMsgCommand(self.account, feedback, [PrivateMessagePort(meta.user_id)])]
                else:
                    try:
                        bot_commands += command.process_msg(msg, meta, hint)
                    except Exception:
                        # 发现未处理的错误, 汇报给主Master
                        bot_commands += self.handle_exception(f"来源:{msg} 用户:{meta.user_id} 群:{meta.group_id} CODE101")
                if not should_pass:
                    break
        if self.proxy:
            for command in bot_commands:
                await self.proxy.process_bot_command(command)
        return bot_commands

    def process_request(self, data: RequestData) -> bool:
        if isinstance(data, FriendRequestData):
            from bot_config import CFG_FRIEND_TOKEN
            passwords: List[str] = self.cfg_helper.get_config(CFG_FRIEND_TOKEN)
            passwords = [password.strip() for password in passwords]
            comment: str = data.comment.strip()
            return not passwords or comment in passwords
        elif isinstance(data, JoinGroupRequestData):
            return True
        elif isinstance(data, InviteGroupRequestData):
            return True
        return False

    async def process_notice(self, data: NoticeData) -> List:
        from src.plugins.DicePP import BotCommandBase
        bot_commands: List[BotCommandBase] = []

        if isinstance(data, FriendAddNoticeData):
            feedback = self.loc_helper.format_loc_text(localization.LOC_FRIEND_ADD_NOTICE)
            from command import PrivateMessagePort, BotSendMsgCommand
            bot_commands += [BotSendMsgCommand(self.account, feedback, [PrivateMessagePort(data.user_id)])]
        elif isinstance(data, GroupIncreaseNoticeData):
            data: GroupIncreaseNoticeData = data
            if data.user_id != self.account:
                from command.impl import DC_WELCOME, LOC_WELCOME_DEFAULT
                from command import GroupMessagePort, BotSendMsgCommand
                try:
                    feedback = self.data_manager.get_data(DC_WELCOME, [data.group_id])
                except DataManagerError:
                    feedback = self.loc_helper.format_loc_text(LOC_WELCOME_DEFAULT)

                if feedback:
                    bot_commands += [BotSendMsgCommand(self.account, feedback, [GroupMessagePort(data.group_id)])]

        if self.proxy:
            for command in bot_commands:
                await self.proxy.process_bot_command(command)
        return bot_commands

    def handle_exception(self, info: str) -> List:
        from command import PrivateMessagePort, BotSendMsgCommand
        exception_info = get_exception_info()
        exception_info = "\n".join(exception_info[-8:]) if len(exception_info) > 8 else "\n".join(exception_info)
        additional_info = f"\n{info}" if info else ""
        feedback = f"未处理的错误:\n{exception_info}{additional_info}"
        from bot_config import CFG_MASTER
        master_list = self.cfg_helper.get_config(CFG_MASTER)
        if master_list:
            return [BotSendMsgCommand(self.account, feedback, [PrivateMessagePort(master_list[0])])]
        else:
            return []

    def get_nickname(self, user_id: str, group_id: str = "") -> str:
        """
        获取用户昵称
        Args:
            user_id: 账号
            group_id: 群号, 为空代表默认
        """
        if not group_id:
            group_id = "default"

        try:
            nickname = self.data_manager.get_data("nickname", [user_id, group_id])  # 使用用户在群内的昵称
        except DataManagerError:
            try:
                nickname = self.data_manager.get_data("nickname", [user_id, "default"])  # 使用用户定义的默认昵称
            except DataManagerError:
                try:
                    nickname = self.data_manager.get_data("nickname", [user_id, "origin"])  # 使用用户本身的用户名
                except DataManagerError:
                    nickname = "异常用户名"
        return nickname

    def update_nickname(self, user_id: str, group_id: str = "", nickname: str = ""):
        """
        更新昵称
        Args:
            user_id: 账号
            group_id: 群号, 为空代表默认昵称, 为origin代表账号本身的名称, origin应该只在process_message时更新
            nickname: 昵称
        """
        if not group_id:
            group_id = "default"
        nickname_prev = self.data_manager.get_data("nickname", [user_id, group_id], nickname)
        if nickname_prev != nickname:
            self.data_manager.set_data("nickname", [user_id, group_id], nickname)
