import os
import asyncio
from typing import List, Optional, Dict, Iterable

import bot_config
import bot_utils
from bot_core import MessageMetaData, NoticeData, RequestData
from bot_core import FriendRequestData, JoinGroupRequestData, InviteGroupRequestData
from bot_core import FriendAddNoticeData, GroupIncreaseNoticeData
from bot_core import BotMacro, MACRO_PARSE_LIMIT
from data_manager import DataManager, DataManagerError, custom_data_chunk, DataChunkBase
import localization
from localization import LocalizationHelper
from bot_config import ConfigHelper, CFG_COMMAND_SPLIT
from logger import dice_log, get_exception_info

DC_META = "meta"
DCP_META_ONLINE_LAST = ["online", "last"]
DCP_META_ONLINE_PERIOD = ["online", "period"]

DC_MACRO = "macro"
DC_USER_DATA = "user_data"
DC_GROUP_DATA = "group_data"
DC_NICKNAME = "nickname"
NICKNAME_ERROR = "Undefined Name"


@custom_data_chunk(identifier=DC_META)
class _(DataChunkBase):
    def __init__(self):
        super().__init__()


@custom_data_chunk(identifier=DC_MACRO, include_json_object=True)
class _(DataChunkBase):
    def __init__(self):
        super().__init__()


@custom_data_chunk(identifier=DC_USER_DATA)
class _(DataChunkBase):
    def __init__(self):
        super().__init__()


@custom_data_chunk(identifier=DC_GROUP_DATA)
class _(DataChunkBase):
    def __init__(self):
        super().__init__()


@custom_data_chunk(identifier=DC_NICKNAME)
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

        self.tick_task: Optional[asyncio.Task] = None

        self.start_up()

    def set_client_proxy(self, proxy):
        from adapter import ClientProxy
        if isinstance(proxy, ClientProxy):
            self.proxy = proxy
        else:
            raise TypeError("Incorrect Client Proxy!")

    def start_up(self):
        self.register_command()
        self.loc_helper.load_localization()  # 要在注册完命令后再读取本地化文件
        self.loc_helper.save_localization()  # 更新本地文件
        self.cfg_helper.load_config()
        self.cfg_helper.save_config()

        try:
            asyncio.get_running_loop()
            self.tick_task = asyncio.create_task(self.tick_loop())
        except RuntimeError:  # 在Debug中
            pass

    async def tick_loop(self):
        from command import BotCommandBase
        loop = asyncio.get_event_loop()
        loop_time_prev = loop.time()

        init_online_str = bot_utils.time.get_current_date_str()
        online_period = self.data_manager.get_data(DC_META, DCP_META_ONLINE_PERIOD, default_val=[])
        online_period.append([init_online_str, init_online_str])

        while True:
            bot_commands: List[BotCommandBase] = []
            # tick
            for command in self.command_dict.values():
                try:
                    bot_commands += command.tick()
                except Exception:
                    dice_log(self.handle_exception(f"Tick: {command.readable_name} CODE110"))

            if loop.time() - loop_time_prev > 60:  # 一分钟执行一次
                # tick_daily
                last_online_str = self.data_manager.get_data(DC_META, DCP_META_ONLINE_LAST, default_val=init_online_str)
                last_online_day_str = bot_utils.time.datetime_to_str_day(bot_utils.time.str_to_datetime(last_online_str))
                cur_online_day_str = bot_utils.time.datetime_to_str_day(bot_utils.time.get_current_date_raw())
                if cur_online_day_str != last_online_day_str:  # 最后在线时间和当前时间不是同一天
                    for command in self.command_dict.values():
                        try:
                            bot_commands += command.tick_daily()
                        except Exception:
                            dice_log(self.handle_exception(f"Tick Daily: {command.readable_name} CODE111"))
                # 更新最后在线时间
                cur_online_str = bot_utils.time.get_current_date_str()
                online_period[-1][-1] = cur_online_str
                self.data_manager.set_data(DC_META, DCP_META_ONLINE_LAST, cur_online_str)
                self.data_manager.set_data(DC_META, DCP_META_ONLINE_PERIOD, online_period)

                loop_time_prev = loop.time()

            if self.proxy:
                for command in bot_commands:
                    await self.proxy.process_bot_command(command)

            await asyncio.sleep(1)

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
        if self.tick_task:
            self.tick_task.cancel()
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
        """处理消息"""
        from command import preprocess_msg
        from command import MessagePort, PrivateMessagePort, BotCommandBase, BotSendMsgCommand

        self.update_nickname(meta.user_id, "origin", meta.nickname)

        msg = preprocess_msg(msg)  # 转换中文符号, 转换小写等等

        bot_commands: List[BotCommandBase] = []

        # 处理宏
        macro_list: List[BotMacro]
        try:
            assert not msg.startswith(".define")
            macro_list = self.data_manager.get_data(DC_MACRO, [meta.user_id], get_ref=True)
        except (DataManagerError, AssertionError):
            macro_list = []
        for macro in macro_list:
            msg = macro.process(msg)
            if len(msg) > MACRO_PARSE_LIMIT:
                break

        # 处理分行指令
        command_split: str = self.cfg_helper.get_config(CFG_COMMAND_SPLIT)[0]
        msg_list = msg.split(command_split)
        msg_list = [m.strip() for m in msg_list]

        is_multi_command = len(msg_list) > 1
        for msg_cur in msg_list:
            for command in self.command_dict.values():
                try:
                    should_proc, should_pass, hint = command.can_process_msg(msg_cur, meta)
                except Exception:
                    # 发现未处理的错误, 汇报给主Master
                    should_proc, should_pass, hint = False, False, None
                    info = f"{msg_list}中的{msg_cur}" if is_multi_command else msg
                    bot_commands += self.handle_exception(f"来源:{info}\n用户:{meta.user_id} 群:{meta.group_id} CODE100")
                if should_proc:
                    if command.group_only and not meta.group_id:
                        # 在非群聊中企图执行群聊指令, 回复一条提示
                        feedback = self.loc_helper.format_loc_text(localization.LOC_GROUP_ONLY_NOTICE)
                        bot_commands += [BotSendMsgCommand(self.account, feedback, [PrivateMessagePort(meta.user_id)])]
                    else:  # 执行指令
                        try:
                            bot_commands += command.process_msg(msg_cur, meta, hint)
                        except Exception:
                            # 发现未处理的错误, 汇报给主Master
                            info = f"{msg_list}中的{msg_cur}" if is_multi_command else msg
                            bot_commands += self.handle_exception(f"来源:{info}\n用户:{meta.user_id} 群:{meta.group_id} CODE101")
                    if not should_pass:
                        break

        if is_multi_command:  # 多行指令的话合并port相同的send msg
            invalid_command_count = 0
            send_msg_command_merged: Dict[MessagePort, BotSendMsgCommand] = {}
            for command in bot_commands:
                if isinstance(command, BotSendMsgCommand):
                    for port in command.targets:
                        if port in send_msg_command_merged:
                            send_msg_command_merged[port].msg += f"\n{command.msg}"
                        else:
                            send_msg_command_merged[port] = BotSendMsgCommand(self.account, command.msg, [port])
                    invalid_command_count += 1
            if invalid_command_count == len(bot_commands):  # 全都是SendMsg则合并
                bot_commands = list(send_msg_command_merged.values())

        if self.proxy:
            for command in bot_commands:
                await self.proxy.process_bot_command(command)
        return bot_commands

    def process_request(self, data: RequestData) -> Optional[bool]:
        """处理请求"""
        if isinstance(data, FriendRequestData):
            from bot_config import CFG_FRIEND_TOKEN
            passwords: List[str] = self.cfg_helper.get_config(CFG_FRIEND_TOKEN)
            passwords = [password.strip() for password in passwords]
            comment: str = data.comment.strip()
            return not passwords or comment in passwords
        elif isinstance(data, JoinGroupRequestData):
            return None
        elif isinstance(data, InviteGroupRequestData):
            from bot_config import CFG_GROUP_INVITE
            should_allow: int = int(self.cfg_helper.get_config(CFG_GROUP_INVITE)[0])
            return should_allow == 1
        return False

    async def process_notice(self, data: NoticeData) -> List:
        """处理提醒"""
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
        """在捕获异常后的Except语句中调用"""
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
            nickname = self.data_manager.get_data(DC_NICKNAME, [user_id, group_id])  # 使用用户在群内的昵称
        except DataManagerError:
            try:
                nickname = self.data_manager.get_data(DC_NICKNAME, [user_id, "default"])  # 使用用户定义的默认昵称
            except DataManagerError:
                try:
                    nickname = self.data_manager.get_data(DC_NICKNAME, [user_id, "origin"])  # 使用用户本身的用户名
                except DataManagerError:
                    nickname = NICKNAME_ERROR
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
        nickname_prev = self.data_manager.get_data(DC_NICKNAME, [user_id, group_id], nickname)
        if nickname_prev != nickname:
            self.data_manager.set_data(DC_NICKNAME, [user_id, group_id], nickname)
