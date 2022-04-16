import os
import asyncio
import datetime
import random
from typing import List, Optional, Dict, Callable, Union, Set

from utils.logger import dice_log, get_exception_info
from utils.time import str_to_datetime, get_current_date_str, get_current_date_raw, int_to_datetime
from core.localization import LocalizationManager, LOC_GROUP_ONLY_NOTICE, LOC_FRIEND_ADD_NOTICE, LOC_GROUP_EXPIRE_WARNING
from core.config import ConfigManager, CFG_COMMAND_SPLIT, CFG_MASTER, CFG_FRIEND_TOKEN, CFG_GROUP_INVITE
from core.config import CFG_DATA_EXPIRE, CFG_USER_EXPIRE_DAY, CFG_GROUP_EXPIRE_DAY, CFG_GROUP_EXPIRE_WARNING,\
    CFG_WHITE_LIST_GROUP, CFG_WHITE_LIST_USER, preprocess_white_list
from core.config import BOT_DATA_PATH, CONFIG_PATH
from core.communication import MessageMetaData, MessagePort, PrivateMessagePort, GroupMessagePort, preprocess_msg
from core.communication import RequestData, FriendRequestData, JoinGroupRequestData, InviteGroupRequestData
from core.communication import NoticeData, FriendAddNoticeData, GroupIncreaseNoticeData
from core.communication import GroupInfo
from core.data import DC_META, DC_NICKNAME, DC_MACRO, DC_VARIABLE, DC_USER_DATA, DC_GROUP_DATA,\
    DCK_META_STAT, DCK_USER_STAT, DCK_GROUP_STAT
from core.data import DataManager, DataManagerError
from core.statistics import MetaStatInfo, GroupStatInfo, UserStatInfo

from core.bot.macro import BotMacro, MACRO_PARSE_LIMIT
from core.bot.variable import BotVariable

NICKNAME_ERROR = "UNDEF_NAME"


# noinspection PyBroadException
class Bot:
    def __init__(self, account: str):
        """
        实例化机器人
        Args:
            account: QQ账号
        """
        import core.command as command
        import module  # module中可能会定义新的DataChunk和local text等, 所以要在一开始import
        from module.dice_hub import HubManager
        from adapter import ClientProxy
        self.account: str = account
        self.proxy: Optional[ClientProxy] = None
        self.data_path = os.path.join(BOT_DATA_PATH, account)

        self.data_manager = DataManager(self.data_path)
        self.fix_data()
        self.hub_manager = HubManager(self)
        self.loc_helper = LocalizationManager(CONFIG_PATH, self.account)
        self.cfg_helper = ConfigManager(CONFIG_PATH, self.account)

        self.command_dict: Dict[str, command.UserCommandBase] = {}

        self.tick_task: Optional[asyncio.Task] = None
        self.todo_tasks: Dict[Union[Callable, asyncio.Task], Dict] = {}

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
        self.loc_helper.load_chat()
        self.loc_helper.save_chat()
        self.cfg_helper.load_config()
        self.cfg_helper.save_config()

        try:
            asyncio.get_running_loop()
            self.tick_task = asyncio.create_task(self.tick_loop())
        except RuntimeError:  # 在Debug中
            pass

    def register_task(self, task: Callable, is_async: bool = True, timeout: float = 10, timeout_callback: Optional[Callable] = None):
        """
        Args:
            task: 等待执行的任务, 必须没有参数, 必须返回 List[BotCommandBase]
            is_async: task是否已经是异步函数, 如不是, 将会在其他线程上运行task; 若为同步函数, timeout必须为0
            timeout: 超时时间, 单位秒, 为0代表不会超时
            timeout_callback: 超时后调用的回调函数, 必须为同步函数, 同样也应该返回 List[BotCommandBase]
        """
        assert is_async or timeout == 0
        self.todo_tasks[task] = {"init": False, "is_async": is_async, "timeout": timeout, "callback": timeout_callback}

    async def tick_loop(self):
        from core.command import BotCommandBase
        loop = asyncio.get_event_loop()
        time_counter = [loop.time()] * 2

        meta_stat: MetaStatInfo = self.data_manager.get_data(DC_META, [DCK_META_STAT], default_gen=MetaStatInfo)
        meta_stat.update(is_first_time=True)

        while True:
            loop_begin_time = loop.time()
            bot_commands: List[BotCommandBase] = []
            try:
                # tick each command
                for command in self.command_dict.values():
                    try:
                        bot_commands += command.tick()
                    except Exception:
                        dice_log(str(self.handle_exception(f"Tick: {command.readable_name} CODE110")[0]))

                if loop_begin_time - time_counter[0] > 60 * 5:  # 5分钟执行一次
                    # 更新在线时间并尝试每日更新
                    if meta_stat.update():
                        await self.tick_daily(bot_commands)
                    # 保存数据到本地
                    await self.data_manager.save_data_async()
                    # 更新计时器
                    time_counter[0] = loop_begin_time

                if loop_begin_time - time_counter[1] > 3600 * 4:  # 4小时执行一次
                    # 更新群信息
                    async def update_group_info():
                        await self.update_group_info_all()
                        return []
                    self.register_task(update_group_info, timeout=3600)
                    # 更新计时器
                    time_counter[1] = loop_begin_time

                if self.todo_tasks:
                    free_time = max(loop_begin_time + 1 - loop.time(), 0.25)
                    await self.process_async_task(bot_commands, free_time, loop)

                if self.proxy:
                    for command in bot_commands:
                        await self.proxy.process_bot_command(command)
            except Exception:
                bot_commands += self.handle_exception(f"Tick Loop: CODE113")

            # 最多每秒执行一次循环
            free_time = max(loop_begin_time + 1 - loop.time(), 0)
            await asyncio.sleep(free_time)

    async def process_async_task(self, bot_commands, free_time: float, loop):
        init_task = [(task, info) for task, info in self.todo_tasks.items() if not info["init"]]
        for func, info in init_task:
            func: Callable
            del self.todo_tasks[func]
            if not info["is_async"]:
                dice_log(f"[Async Task] Init Sync: {func.__name__}")

                async def task_wrapper():
                    future = loop.run_in_executor(None, func)
                    await future
                    return future.result()

                task: asyncio.Task = asyncio.create_task(task_wrapper())
            else:
                dice_log(f"[Async Task] Init Async: {func.__name__}")
                task: asyncio.Task = asyncio.create_task(func())
            info["init"] = True
            self.todo_tasks[task] = info

        dice_log(f"[Async Task] Try: "
                 f"{[(task.get_coro().cr_code.co_name, self.todo_tasks[task]['timeout']) for task in self.todo_tasks.keys()]}"
                 f" for {free_time} s")
        try:
            done_tasks, pending_tasks = await asyncio.wait(self.todo_tasks.keys(), timeout=free_time)
            task: asyncio.Task
            for task in done_tasks:
                try:
                    bot_commands += task.result()
                except Exception:
                    dice_log(str(self.handle_exception(f"Async Task: CODE114")[0]))
                del self.todo_tasks[task]
                dice_log(f"[Async Task] Finish {task.get_coro().cr_code.co_name}")
            for task in pending_tasks:
                if self.todo_tasks[task]["timeout"] > 0:
                    self.todo_tasks[task]["timeout"] -= free_time
                    if self.todo_tasks[task]["timeout"] < 0:
                        dice_log(f"[Async Task] Timeout: {task.get_coro().cr_code.co_name}")
                        if self.todo_tasks[task]["callback"]:
                            dice_log(f"[Async Task] Timeout callback: {self.todo_tasks[task]['callback'].__name__}")
                            bot_commands += self.todo_tasks[task]["callback"]()
                        task.cancel()
                        del self.todo_tasks[task]
        except Exception:
            dice_log(str(self.handle_exception(f"Async Task: CODE112")[0]))

    async def tick_daily(self, bot_commands):
        # 更新用户统计
        for user_id in self.data_manager.get_keys(DC_USER_DATA, []):
            try:
                user_stat: UserStatInfo = self.data_manager.get_data(DC_USER_DATA, [user_id, DCK_USER_STAT], get_ref=True)
            except DataManagerError:
                continue
            user_stat.daily_update()
        # 更新群聊统计
        for group_id in self.data_manager.get_keys(DC_GROUP_DATA, []):
            try:
                group_stat: GroupStatInfo = self.data_manager.get_data(DC_USER_DATA, [group_id, DCK_GROUP_STAT], get_ref=True)
            except DataManagerError:
                continue
            group_stat.daily_update()

        # 尝试清理过期群聊和过期用户信息
        async def clear_expired_data():
            res = await self.clear_expired_data()
            return res

        self.register_task(clear_expired_data, timeout=3600)

        # 调用每个command的tick_daily方法
        for command in self.command_dict.values():
            try:
                bot_commands += command.tick_daily()
            except Exception:
                dice_log(str(self.handle_exception(f"Tick Daily: {command.readable_name} CODE111")[0]))
        # 给Master发送每日更新通知
        from core.localization import LOC_DAILY_UPDATE
        feedback = self.loc_helper.format_loc_text(LOC_DAILY_UPDATE)
        if feedback and feedback != "$":
            await self.send_msg_to_master(feedback)

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
        import sys
        python = sys.executable
        os.execl(python, python, *sys.argv)
        # self.start_up()
        # await self.delay_init_command()

    def register_command(self):
        from core.command.user_cmd import USER_COMMAND_CLS_DICT
        command_cls_dict = USER_COMMAND_CLS_DICT
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
            from core.command import BotSendMsgCommand
            from core.localization import LOC_LOGIN_NOTICE
            feedback = self.loc_helper.format_loc_text(LOC_LOGIN_NOTICE)
            if feedback and feedback != "$":
                feedback = f"{init_info}\n{feedback}"
                dice_log(feedback)
                # 给所有Master汇报
                for master in self.cfg_helper.get_config(CFG_MASTER):
                    command = BotSendMsgCommand(self.account, feedback, [PrivateMessagePort(master)])
                    await self.proxy.process_bot_command(command)
            else:
                dice_log(init_info)

    # noinspection PyBroadException
    async def process_message(self, msg: str, meta: MessageMetaData) -> List:
        """处理消息"""
        from core.command import BotCommandBase, BotSendMsgCommand

        self.update_nickname(meta.user_id, "origin", meta.nickname)

        msg = preprocess_msg(msg)  # 转换中文符号, 转换小写等等

        bot_commands: List[BotCommandBase] = []

        # 统计信息
        meta_stat: MetaStatInfo = self.data_manager.get_data(DC_META, [DCK_META_STAT], default_gen=MetaStatInfo, get_ref=True)
        user_stat: UserStatInfo = self.data_manager.get_data(DC_USER_DATA, [meta.user_id, DCK_USER_STAT], default_gen=UserStatInfo, get_ref=True)
        if meta.group_id:
            group_stat: GroupStatInfo = self.data_manager.get_data(DC_GROUP_DATA, [meta.group_id, DCK_GROUP_STAT],
                                                                   default_gen=GroupStatInfo, get_ref=True)
        else:
            group_stat = GroupStatInfo()
        # 统计收到的消息数量
        meta_stat.msg.inc()
        group_stat.msg.inc()
        user_stat.msg.inc()

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

        # 处理变量
        try:
            var_name_list: List[str] = self.data_manager.get_keys(DC_VARIABLE, [meta.user_id, meta.group_id])
        except DataManagerError:
            var_name_list = []
        for var_name in var_name_list:
            key = f"%{var_name}%"
            if key in msg:
                var: BotVariable = self.data_manager.get_data(DC_VARIABLE, [meta.user_id, meta.group_id, var_name])
                msg = msg.replace(key, str(var.val))

        # 处理分行指令
        command_split: str = self.cfg_helper.get_config(CFG_COMMAND_SPLIT)[0]
        msg_list = msg.split(command_split)
        msg_list = [m.strip() for m in msg_list]
        is_multi_command = len(msg_list) > 1

        # 遍历所有指令, 尝试处理消息
        for msg_cur in msg_list:
            for command in self.command_dict.values():
                # 判断是否能处理该条指令
                try:
                    should_proc, should_pass, hint = command.can_process_msg(msg_cur, meta)
                except Exception:
                    # 发现未处理的错误, 汇报给主Master
                    should_proc, should_pass, hint = False, False, None
                    info = f"{msg_list}中的{msg_cur}" if is_multi_command else msg
                    group_info = f"群:{meta.group_id}" if meta.group_id else "私聊"
                    bot_commands += self.handle_exception(f"来源:{info}\n用户:{meta.user_id} {group_info} CODE100")
                if not should_proc:
                    continue
                # 在非群聊中企图执行群聊指令, 回复一条提示
                if command.group_only and not meta.group_id:
                    feedback = self.loc_helper.format_loc_text(LOC_GROUP_ONLY_NOTICE)
                    bot_commands += [BotSendMsgCommand(self.account, feedback, [PrivateMessagePort(meta.user_id)])]
                    break
                # 执行指令
                res_commands = []
                try:
                    res_commands = command.process_msg(msg_cur, meta, hint)
                    bot_commands += res_commands
                except Exception:
                    # 发现未处理的错误, 汇报给主Master
                    info = f"{msg_list}中的{msg_cur}" if is_multi_command else msg
                    group_info = f"群:{meta.group_id}" if meta.group_id else "私聊"
                    bot_commands += self.handle_exception(f"来源:{info}\n用户:{meta.user_id} {group_info} CODE101")

                # 统计处理的指令情况
                if command.flag and res_commands:
                    meta_stat.cmd.record(command)
                    user_stat.cmd.record(command)
                    group_stat.cmd.record(command)

                if not should_pass:  # 已经处理过, 不需要再传递给后面的指令
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

        if self.proxy and bot_commands:
            # 处理指令
            await self.proxy.process_bot_command_list(bot_commands)
        return bot_commands

    def process_request(self, data: RequestData) -> Optional[bool]:
        """处理请求"""
        if isinstance(data, FriendRequestData):
            passwords: List[str] = self.cfg_helper.get_config(CFG_FRIEND_TOKEN)
            passwords = [password.strip() for password in passwords if password.strip()]
            comment: str = data.comment.strip()
            return not passwords or comment in passwords
        elif isinstance(data, JoinGroupRequestData):
            return None
        elif isinstance(data, InviteGroupRequestData):
            should_allow: int = int(self.cfg_helper.get_config(CFG_GROUP_INVITE)[0])
            return should_allow == 1
        return False

    async def process_notice(self, data: NoticeData) -> List:
        """处理提醒"""
        from core.command import BotCommandBase, BotSendMsgCommand
        from module.common import DC_ACTIVATE, DC_WELCOME, LOC_WELCOME_DEFAULT
        bot_commands: List[BotCommandBase] = []

        if isinstance(data, FriendAddNoticeData):
            feedback = self.loc_helper.format_loc_text(LOC_FRIEND_ADD_NOTICE)
            bot_commands += [BotSendMsgCommand(self.account, feedback, [PrivateMessagePort(data.user_id)])]
        elif isinstance(data, GroupIncreaseNoticeData):
            data: GroupIncreaseNoticeData = data
            if data.user_id != self.account:
                try:
                    activate = self.data_manager.get_data(DC_ACTIVATE, [data.group_id])[0]
                except DataManagerError:
                    activate = False

                if activate:
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
        from core.command import BotSendMsgCommand
        exception_info = get_exception_info()
        exception_info = "\n".join(exception_info[-8:]) if len(exception_info) > 8 else "\n".join(exception_info)
        additional_info = f"\n{info}" if info else ""
        feedback = f"未处理的错误:\n{exception_info}{additional_info}"
        master_list = self.cfg_helper.get_config(CFG_MASTER)
        if master_list:
            return [BotSendMsgCommand(self.account, feedback, [PrivateMessagePort(master_list[0])])]
        else:
            return []

    def get_master_ids(self) -> List[str]:
        return self.cfg_helper.get_config(CFG_MASTER)

    async def send_msg_to_master(self, msg: str) -> None:
        """发送信息给主Master"""
        from core.command import BotSendMsgCommand
        master_list = self.get_master_ids()
        if master_list:
            await self.proxy.process_bot_command(BotSendMsgCommand(self.account, msg, [PrivateMessagePort(master_list[0])]))

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

    async def update_group_info_all(self) -> List[GroupInfo]:
        if not self.proxy:
            return []
        group_info_list: List[GroupInfo] = await self.proxy.get_group_list()
        all_group_id = set(self.data_manager.get_keys(DC_GROUP_DATA, []))
        valid_group_id = set((info.group_id for info in group_info_list))
        for info in group_info_list:
            group_stat: GroupStatInfo = self.data_manager.get_data(DC_GROUP_DATA, [info.group_id, DCK_GROUP_STAT],
                                                                   default_gen=GroupStatInfo, get_ref=True)
            group_stat.meta.update(info.group_name, info.member_count, info.max_member_count)
        for group_id in all_group_id.difference(valid_group_id):
            group_stat: GroupStatInfo = self.data_manager.get_data(DC_GROUP_DATA, [group_id, DCK_GROUP_STAT],
                                                                   default_gen=GroupStatInfo, get_ref=True)
            group_stat.meta.member_count = -1
            group_stat.meta.max_member = -1

        return group_info_list

    def fix_data(self):
        pass

    async def clear_expired_data(self) -> List:
        from core.command import BotSendMsgCommand, BotDelayCommand, BotLeaveGroupCommand, BotCommandBase
        from module.common import DC_POINT, DC_WELCOME, DC_ACTIVATE, DC_CHAT_RECORD
        from module.character.dnd5e import DC_CHAR_DND, DC_CHAR_HP
        from module.initiative import DC_INIT

        cur_date = get_current_date_raw()
        try:
            is_data_expire = bool(int(self.cfg_helper.get_config(CFG_DATA_EXPIRE)[0]))
            user_expire_day = int(self.cfg_helper.get_config(CFG_USER_EXPIRE_DAY)[0])
            group_expire_day = int(self.cfg_helper.get_config(CFG_GROUP_EXPIRE_DAY)[0])
            group_expire_time = int(self.cfg_helper.get_config(CFG_GROUP_EXPIRE_WARNING)[0])
            group_expire_warn = self.loc_helper.format_loc_text(LOC_GROUP_EXPIRE_WARNING)
        except ValueError:
            return self.handle_exception(f"自动清理信息")
        if not is_data_expire:
            return []
        result_commands: List[BotCommandBase] = []
        index = 0

        white_list_group: List[str] = preprocess_white_list(self.cfg_helper.get_config(CFG_WHITE_LIST_GROUP))
        white_list_user: List[str] = preprocess_white_list(self.cfg_helper.get_config(CFG_WHITE_LIST_USER))

        # 清理过期用户信息
        all_user_id: Set[str] = set(self.data_manager.get_keys(DC_USER_DATA, []))
        # all_user_id.union(set(self.data_manager.get_keys(DC_NICKNAME, [])))
        invalid_user_id = []
        for user_id in all_user_id:
            is_valid = False
            # 白名单中的用户不会被清理
            if user_id in white_list_user:
                continue
            try:
                user_stat: UserStatInfo = self.data_manager.get_data(DC_USER_DATA, [user_id, DCK_USER_STAT])
            except DataManagerError:
                invalid_user_id.append(user_id)
                continue
            # 掷骰次数超过一定次数的用户不会被清理
            if user_stat.roll.times.total_val > 200:
                is_valid = True
            # 过去一段时间内使用过指令的用户不会被清理
            for flag in user_stat.cmd.flag_dict.keys():
                flag_date = int_to_datetime(user_stat.cmd.flag_dict[flag].update_time)
                if cur_date - flag_date < datetime.timedelta(days=user_expire_day):
                    is_valid = True
                    break

            if not is_valid:
                invalid_user_id.append(user_id)
            index += 1
            if index % 500 == 0:
                await asyncio.sleep(0)
        for user_id in invalid_user_id:
            self.data_manager.delete_data(DC_USER_DATA, [user_id])
            self.data_manager.delete_data(DC_NICKNAME, [user_id])
            self.data_manager.delete_data(DC_MACRO, [user_id])
            self.data_manager.delete_data(DC_VARIABLE, [user_id])
            self.data_manager.delete_data(DC_POINT, [user_id])
            self.data_manager.delete_data(DC_CHAT_RECORD, [user_id])

        # 清理过期群聊消息
        all_group_id: Set[str] = set(self.data_manager.get_keys(DC_GROUP_DATA, []))
        invalid_group_id = []
        warning_group_id = []
        for group_id in all_group_id:
            is_valid = False
            # 白名单中的群聊不会被清理
            if group_id in white_list_group:
                continue
            try:
                group_stat: GroupStatInfo = self.data_manager.get_data(DC_GROUP_DATA, [group_id, DCK_GROUP_STAT], get_ref=True)
            except DataManagerError:
                invalid_group_id.append(group_id)
                continue
            # 过去一段时间内使用过指令的群不会被清理
            for flag in group_stat.cmd.flag_dict.keys():
                flag_date = int_to_datetime(group_stat.cmd.flag_dict[flag].update_time)
                if cur_date - flag_date < datetime.timedelta(days=group_expire_day):
                    is_valid = True
                    break
            # 对还没有到达警告次数上限的群进行警告, 不会进行清理
            if not is_valid and group_stat.meta.warn_time < group_expire_time:
                is_valid = True
                group_stat.meta.warn_time += 1
                if group_stat.meta.member_count > 0:  # 只对拥有群成员的群发送警告消息, 没有说明已经不在该群了
                    result_commands.append(BotDelayCommand(self.account, seconds=random.random() * 10 + 2))
                    result_commands.append(BotSendMsgCommand(self.account, group_expire_warn, [GroupMessagePort(group_id)]))
                    warning_group_id.append(group_id)
            if not is_valid:
                invalid_group_id.append(group_id)
            index += 1
            if index % 500 == 0:
                await asyncio.sleep(0)
        for group_id in invalid_group_id:
            result_commands.append(BotDelayCommand(self.account, seconds=random.random() * 10 + 2))
            temp_warning = "[测试] 该群聊被标记为无效群聊, 尝试使用掷骰指令以清除此标记和提醒"
            result_commands.append(BotSendMsgCommand(self.account, temp_warning, [GroupMessagePort(group_id)]))
            # result_commands.append(BotLeaveGroupCommand(self.account, group_id))
            # self.data_manager.delete_data(DC_GROUP_DATA, [group_id])
            # self.data_manager.delete_data(DC_WELCOME, [group_id])
            # self.data_manager.delete_data(DC_ACTIVATE, [group_id])
            # self.data_manager.delete_data(DC_CHAR_DND, [group_id])
            # self.data_manager.delete_data(DC_CHAR_HP, [group_id])
            # self.data_manager.delete_data(DC_INIT, [group_id])

        # 给Master汇报清理情况
        if self.get_master_ids():
            master_id = self.get_master_ids()[0]
            result_commands.append(BotDelayCommand(self.account, seconds=random.random() * 10 + 2))
            feedback = f"检查{len(all_user_id)}个用户数据, {len(all_group_id)}个群聊数据.\n" \
                       f"清理{len(invalid_user_id)}个失效用户, {len(invalid_group_id)}个失效群聊({invalid_group_id}).\n" \
                       f"对{len(warning_group_id)}个即将失效的群聊发送提示消息."
            result_commands.append(BotSendMsgCommand(self.account, feedback, [PrivateMessagePort(master_id)]))
        result_commands = list(reversed(result_commands))
        return result_commands
