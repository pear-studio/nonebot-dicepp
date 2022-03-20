import os
import asyncio
import datetime
import random
from typing import List, Optional, Dict, Callable, Union, Set

from utils.logger import dice_log, get_exception_info
from utils.time import str_to_datetime, datetime_to_str_day, get_current_date_str, get_current_date_raw
from core.localization import LocalizationManager, LOC_GROUP_ONLY_NOTICE, LOC_FRIEND_ADD_NOTICE, LOC_GROUP_EXPIRE_WARNING
from core.config import ConfigManager, CFG_COMMAND_SPLIT, CFG_MASTER, CFG_FRIEND_TOKEN, CFG_GROUP_INVITE
from core.config import CFG_DATA_EXPIRE, CFG_USER_EXPIRE_DAY, CFG_GROUP_EXPIRE_DAY, CFG_GROUP_EXPIRE_WARNING
from core.config import BOT_DATA_PATH, CONFIG_PATH
from core.communication import MessageMetaData, MessagePort, PrivateMessagePort, GroupMessagePort, preprocess_msg
from core.communication import RequestData, FriendRequestData, JoinGroupRequestData, InviteGroupRequestData
from core.communication import NoticeData, FriendAddNoticeData, GroupIncreaseNoticeData
from core.communication import GroupInfo
from core.data import DC_META, DC_NICKNAME, DC_MACRO, DC_VARIABLE, DC_USER_DATA, DC_GROUP_DATA
from core.data import DCK_TOTAL_NUM, DCK_TODAY_NUM, DCK_LAST_NUM, DCK_LAST_TIME
from core.data import DCP_META_ONLINE_PERIOD, DCP_META_ONLINE_LAST
from core.data import DCP_META_CMD_TOTAL_NUM, DCP_META_CMD_TODAY_NUM, DCP_META_CMD_LAST_NUM
from core.data import DCP_META_MSG_TOTAL_NUM, DCP_META_MSG_TODAY_NUM, DCP_META_MSG_LAST_NUM
from core.data import DCP_USER_CMD_FLAG_A_UID, DCP_USER_META_A_UID, DCP_GROUP_CMD_FLAG_A_GID, DCP_GROUP_INFO_A_GID, DCP_GROUP_META_A_GID
from core.data import DataManager, DataManagerError

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

        init_online_str = get_current_date_str()
        online_period = self.data_manager.get_data(DC_META, DCP_META_ONLINE_PERIOD, default_val=[])
        online_period.append([init_online_str, init_online_str])

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
                    # 尝试每日更新
                    last_online_str = self.data_manager.get_data(DC_META, DCP_META_ONLINE_LAST, default_val=init_online_str)
                    last_online_day_str = datetime_to_str_day(str_to_datetime(last_online_str))
                    cur_online_day_str = datetime_to_str_day(get_current_date_raw())
                    if cur_online_day_str != last_online_day_str:  # 最后在线时间和当前时间不是同一天
                        await self.tick_daily(bot_commands)
                    # 更新最后在线时间
                    cur_online_str = get_current_date_str()
                    online_period[-1][-1] = cur_online_str
                    self.data_manager.set_data(DC_META, DCP_META_ONLINE_LAST, cur_online_str)
                    self.data_manager.set_data(DC_META, DCP_META_ONLINE_PERIOD, online_period)
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
            except Exception:
                bot_commands += self.handle_exception(f"Tick Loop: CODE113")

            if self.proxy:
                for command in bot_commands:
                    await self.proxy.process_bot_command(command)

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
                bot_commands += task.result()
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
        # 处理Meta统计
        meta_msg_last = self.data_manager.get_data(DC_META, DCP_META_MSG_TODAY_NUM, default_val=0)
        meta_cmd_last = self.data_manager.get_data(DC_META, DCP_META_CMD_TODAY_NUM, default_val=0)
        self.data_manager.set_data(DC_META, DCP_META_MSG_LAST_NUM, meta_msg_last)
        self.data_manager.set_data(DC_META, DCP_META_CMD_LAST_NUM, meta_cmd_last)
        self.data_manager.set_data(DC_META, DCP_META_MSG_TODAY_NUM, 0)
        self.data_manager.set_data(DC_META, DCP_META_CMD_TODAY_NUM, 0)
        # 处理指令Flag统计
        from core.command.const import DPP_COMMAND_FLAG_DICT

        def process_cmd_flag_info(cmd_flag_info):
            for flag in DPP_COMMAND_FLAG_DICT.keys():
                if flag in cmd_flag_info:
                    cmd_flag_info[flag][DCK_LAST_TIME] = cmd_flag_info[flag][DCK_TODAY_NUM]
                    cmd_flag_info[flag][DCK_TODAY_NUM] = 0
        for user_id in self.data_manager.get_keys(DC_USER_DATA, []):
            user_cmd_flag_path = [user_id] + DCP_USER_CMD_FLAG_A_UID
            user_cmd_flag_info = self.data_manager.get_data(DC_USER_DATA, user_cmd_flag_path, default_val={}, get_ref=True)
            process_cmd_flag_info(user_cmd_flag_info)

        for group_id in self.data_manager.get_keys(DC_GROUP_DATA, []):
            group_cmd_flag_path = [group_id] + DCP_GROUP_CMD_FLAG_A_GID
            group_cmd_flag_info = self.data_manager.get_data(DC_USER_DATA, group_cmd_flag_path, default_val={}, get_ref=True)
            process_cmd_flag_info(group_cmd_flag_info)

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

        # 统计收到的消息数量
        meta_msg_total_num = self.data_manager.get_data(DC_META, DCP_META_MSG_TOTAL_NUM, default_val=0)
        meta_msg_today_num = self.data_manager.get_data(DC_META, DCP_META_MSG_TODAY_NUM, default_val=0)
        self.data_manager.set_data(DC_META, DCP_META_MSG_TOTAL_NUM, meta_msg_total_num+1)
        self.data_manager.set_data(DC_META, DCP_META_MSG_TODAY_NUM, meta_msg_today_num+1)

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
                try:
                    bot_commands += command.process_msg(msg_cur, meta, hint)
                except Exception:
                    # 发现未处理的错误, 汇报给主Master
                    info = f"{msg_list}中的{msg_cur}" if is_multi_command else msg
                    group_info = f"群:{meta.group_id}" if meta.group_id else "私聊"
                    bot_commands += self.handle_exception(f"来源:{info}\n用户:{meta.user_id} {group_info} CODE101")

                cur_date = get_current_date_str()
                # 统计处理的指令情况
                from core.command.const import DPP_COMMAND_FLAG_DICT
                if command.flag:
                    def stat_cmd_flag(cmd_flag_info):
                        for flag in DPP_COMMAND_FLAG_DICT.keys():
                            if flag & command.flag:
                                if flag not in cmd_flag_info:
                                    cmd_flag_info[flag] = {DCK_TOTAL_NUM: 1, DCK_TODAY_NUM: 1, DCK_LAST_NUM: 0, DCK_LAST_TIME: cur_date}
                                else:
                                    cmd_flag_info[flag][DCK_TOTAL_NUM] += 1
                                    cmd_flag_info[flag][DCK_TODAY_NUM] += 1
                                    cmd_flag_info[flag][DCK_LAST_TIME] = cur_date
                    # 统计用户信息
                    user_cmd_flag_path = [meta.user_id] + DCP_USER_CMD_FLAG_A_UID
                    user_cmd_flag_info = self.data_manager.get_data(DC_USER_DATA, user_cmd_flag_path, default_val={}, get_ref=True)
                    stat_cmd_flag(user_cmd_flag_info)
                    # 统计群信息
                    if meta.group_id:
                        group_cmd_flag_path = [meta.group_id] + DCP_GROUP_CMD_FLAG_A_GID
                        group_cmd_flag_info = self.data_manager.get_data(DC_USER_DATA, group_cmd_flag_path, default_val={}, get_ref=True)
                        stat_cmd_flag(group_cmd_flag_info)

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
            # 统计处理的指令数目
            meta_cmd_total_num = self.data_manager.get_data(DC_META, DCP_META_CMD_TOTAL_NUM, default_val=0)
            meta_cmd_today_num = self.data_manager.get_data(DC_META, DCP_META_CMD_TODAY_NUM, default_val=0)
            self.data_manager.set_data(DC_META, DCP_META_CMD_TOTAL_NUM, meta_cmd_total_num + 1)
            self.data_manager.set_data(DC_META, DCP_META_CMD_TODAY_NUM, meta_cmd_today_num + 1)
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
        cur_date_str = get_current_date_str()
        for info in group_info_list:
            info_path = [info.group_id] + DCP_GROUP_INFO_A_GID
            info_dict = {"name": info.group_name, "member_count": info.member_count,
                         "max_member_count": info.max_member_count, "update": cur_date_str}
            self.data_manager.set_data(DC_GROUP_DATA, info_path, info_dict)
        return group_info_list

    async def clear_expired_data(self) -> List:
        from core.command.const import DPP_COMMAND_FLAG_DICT
        from core.command import BotSendMsgCommand, BotDelayCommand, BotLeaveGroupCommand, BotCommandBase
        from module.roll import DCP_USER_DATA_ROLL_A_UID, DCP_ROLL_TIME_A_ID_ROLL, DCK_ROLL_TOTAL
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
            is_data_expire = False
            user_expire_day = 30
            group_expire_day = 7
            group_expire_time = 1
            group_expire_warn = "Anyone needs me?"
        if not is_data_expire:
            return []
        result_commands: List[BotCommandBase] = []
        index = 0

        # 清理过期用户信息
        all_user_id: Set[str] = set(self.data_manager.get_keys(DC_USER_DATA, []))
        all_user_id.union(set(self.data_manager.get_keys(DC_NICKNAME, [])))
        invalid_user_id = []
        for user_id in all_user_id:
            # meta_path = [user_id] + DCP_USER_META_A_UID
            # meta_info = self.data_manager.get_data(DC_USER_DATA, meta_path, default_val={})
            is_valid = False
            # 掷骰次数超过一定次数的用户不会被清理
            try:
                roll_time_path = [user_id] + DCP_USER_DATA_ROLL_A_UID + DCP_ROLL_TIME_A_ID_ROLL + [DCK_ROLL_TOTAL]
                roll_time = self.data_manager.get_data(DC_USER_DATA, roll_time_path)
                if roll_time > 200:
                    is_valid = True
            except DataManagerError:
                pass
            # 过去一段时间内使用过指令的用户不会被清理
            try:
                cmd_flag_path = [user_id] + DCP_USER_CMD_FLAG_A_UID
                cmd_flag_info: Dict = self.data_manager.get_data(DC_USER_DATA, cmd_flag_path)
                for flag in DPP_COMMAND_FLAG_DICT:
                    if flag not in cmd_flag_info:
                        continue
                    flag_date = str_to_datetime(cmd_flag_info[flag][DCK_LAST_TIME])
                    if cur_date - flag_date < datetime.timedelta(days=user_expire_day):
                        is_valid = True
                        break
            except DataManagerError:
                pass
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
            # 过去一段时间内使用过指令的群不会被清理
            try:
                cmd_flag_path = [group_id] + DCP_GROUP_CMD_FLAG_A_GID
                cmd_flag_info: Dict = self.data_manager.get_data(DC_GROUP_DATA, cmd_flag_path)
                for flag in DPP_COMMAND_FLAG_DICT:
                    if flag not in cmd_flag_info:
                        continue
                    flag_date = str_to_datetime(cmd_flag_info[flag][DCK_LAST_TIME])
                    if cur_date - flag_date < datetime.timedelta(days=group_expire_day):
                        is_valid = True
                        break
            except DataManagerError:
                pass
            # 还没有到达警告次数上限的群不会被清理
            meta_path = [group_id] + DCP_GROUP_META_A_GID
            meta_info: Dict = self.data_manager.get_data(DC_GROUP_DATA, meta_path, default_val={})
            if not is_valid and meta_info.get("warn_time", 0) < group_expire_time:
                is_valid = True
                meta_info["warn_time"] = meta_info.get("warn_time", 0) + 1
                self.data_manager.set_data(DC_GROUP_DATA, meta_path, meta_info)
                try:  # 只对拥有Info的群发送警告消息 (没有说明已经不在该群了)
                    info_path = [group_id] + DCP_GROUP_INFO_A_GID
                    self.data_manager.get_data(DC_GROUP_DATA, info_path)
                    result_commands.append(BotDelayCommand(self.account, seconds=random.random() * 10 + 2))
                    result_commands.append(BotSendMsgCommand(self.account, group_expire_warn, [GroupMessagePort(group_id)]))
                    warning_group_id.append(group_id)
                except DataManagerError:
                    pass
            if group_id in all_user_id:  # 有一部分用户被当做群聊记录了, 直接清理掉
                is_valid = False
            if not is_valid:
                invalid_group_id.append(group_id)
            index += 1
            if index % 500 == 0:
                await asyncio.sleep(0)
        for group_id in invalid_group_id:
            # 有一部分用户被当做群聊记录了, 不需要执行退群指令
            if group_id not in all_user_id:
                result_commands.append(BotDelayCommand(self.account, seconds=random.random() * 10 + 2))
                result_commands.append(BotLeaveGroupCommand(self.account, group_id))
            self.data_manager.delete_data(DC_GROUP_DATA, [group_id])
            self.data_manager.delete_data(DC_WELCOME, [group_id])
            self.data_manager.delete_data(DC_ACTIVATE, [group_id])
            self.data_manager.delete_data(DC_CHAR_DND, [group_id])
            self.data_manager.delete_data(DC_CHAR_HP, [group_id])
            self.data_manager.delete_data(DC_INIT, [group_id])

        # 给Master汇报清理情况
        if self.get_master_ids():
            master_id = self.get_master_ids()[0]
            result_commands.append(BotDelayCommand(self.account, seconds=random.random() * 10 + 2))
            feedback = f"检查{len(all_user_id)}个用户数据, {len(all_group_id)}个群聊数据.\n" \
                       f"清理{len(invalid_user_id)}个失效用户, {len(invalid_group_id)}个失效群聊.\n" \
                       f"对{len(warning_group_id)}个即将失效的群聊发送提示消息."
            result_commands.append(BotSendMsgCommand(self.account, feedback, [PrivateMessagePort(master_id)]))
        result_commands = list(reversed(result_commands))
        return result_commands
