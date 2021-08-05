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


@custom_data_chunk(identifier="nickname")
class _(DataChunkBase):
    def __init__(self):
        super().__init__()


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
        """
        销毁bot对象时触发, 可能是bot断连, 或关闭应用导致的
        """
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

    def register_command(self):
        import command
        command_cls_dict = command.dicepp_command.USER_COMMAND_CLS_DICT
        command_names = command_cls_dict.keys()
        command_names = sorted(command_names, key=lambda n: command_cls_dict[n].priority)  # 按优先级排序
        for command_name in command_names:
            command_cls = command_cls_dict[command_name]
            self.command_dict[command_name] = command_cls(bot=self)  # 默认的Dict是有序的, 所以之后用values拿到的也是有序的

    def delay_init(self):
        asyncio.run(self.delay_init_command())

    async def delay_init_command(self):
        error_info: List[str] = []
        for command in self.command_dict.values():
            error_info += command.delay_init()
        if self.proxy:
            from command import PrivateMessagePort, BotSendMsgCommand
            feedback = "完成加载" + "\n".join(error_info)
            from bot_config import CFG_MASTER
            print(feedback)
            master_list = self.cfg_helper.get_config(CFG_MASTER)
            for master in master_list:  # 给Master汇报
                command = BotSendMsgCommand(self.account, feedback, [PrivateMessagePort(master)])
                await self.proxy.process_bot_command(command)

    async def process_message(self, msg: str, meta: MessageMetaData) -> List:
        from command import preprocess_msg
        from src.plugins.DicePP import BotCommandBase

        self.update_nickname(meta.user_id, "origin", meta.nickname)

        msg = preprocess_msg(msg)  # 转换中文符号, 转换小写等等

        bot_commands: List[BotCommandBase] = []

        for command in self.command_dict.values():
            should_proc, should_pass, hint = command.can_process_msg(msg, meta)
            if should_proc:
                if command.group_only and not meta.group_id:
                    # 在非群聊中企图执行群聊指令, 回复一条提示
                    feedback = self.loc_helper.format_loc_text(localization.LOC_GROUP_ONLY_NOTICE)
                    from command import PrivateMessagePort, BotSendMsgCommand
                    bot_commands += [BotSendMsgCommand(self.account, feedback, [PrivateMessagePort(meta.user_id)])]
                else:
                    bot_commands += command.process_msg(msg, meta, hint)
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
            # ToDo .welcome 指令
            if data.user_id != self.account:
                feedback = "欢迎~"
                from command import GroupMessagePort, BotSendMsgCommand
                bot_commands += [BotSendMsgCommand(self.account, feedback, [GroupMessagePort(data.group_id)])]

        if self.proxy:
            for command in bot_commands:
                await self.proxy.process_bot_command(command)
        return bot_commands

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
