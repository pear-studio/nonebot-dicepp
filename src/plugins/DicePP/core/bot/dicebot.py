import os
import asyncio
import datetime
import random
from typing import List, Optional, Dict, Callable, Union, Set
from random import choice

from utils.logger import dice_log, get_exception_info
from utils.time import str_to_datetime, get_current_date_str, get_current_date_raw, int_to_datetime
from core.localization import LocalizationManager, LOC_GROUP_ONLY_NOTICE, LOC_PERMISSION_DENIED_NOTICE, LOC_FRIEND_ADD_NOTICE, LOC_GROUP_EXPIRE_WARNING
from core.config import ConfigManager, CFG_COMMAND_SPLIT, CFG_MASTER, CFG_FRIEND_TOKEN, CFG_GROUP_INVITE
from core.config import CFG_DATA_EXPIRE, CFG_USER_EXPIRE_DAY, CFG_GROUP_EXPIRE_DAY, CFG_GROUP_EXPIRE_WARNING,\
    CFG_WHITE_LIST_GROUP, CFG_WHITE_LIST_USER, CFG_ADMIN, CFG_MASTER, preprocess_white_list
from core.config import CFG_MEMORY_MONITOR_ENABLE, CFG_MEMORY_WARN_PERCENT, CFG_MEMORY_RESTART_PERCENT, CFG_MEMORY_RESTART_MB
from core.config import BOT_DATA_PATH
from core.communication import MessageMetaData, MessagePort, PrivateMessagePort, GroupMessagePort, preprocess_msg
from core.communication import RequestData, FriendRequestData, JoinGroupRequestData, InviteGroupRequestData
from core.communication import NoticeData, FriendAddNoticeData, GroupIncreaseNoticeData
from core.communication import GroupInfo
from core.data import DC_META, DC_NICKNAME, DC_USER_DATA, DC_GROUP_DATA,\
    DCK_META_STAT, DCK_USER_STAT, DCK_GROUP_STAT
from core.data import BotDatabase
from core.data.models import UserStat, GroupStat, MetaStat, BotControl, UserNickname
from core.statistics import MetaStatInfo, GroupStatInfo, UserStatInfo

import shutil

# 日志清理相关常量
LOGS_SUBDIR = "logs"
LOG_RETENTION_SECONDS = 24 * 3600  # 24小时

# 内存监控
try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False

NICKNAME_ERROR = "UNDEF_NAME"


# noinspection PyBroadException
class Bot:
    def __init__(self, account: str, readonly: bool = False, no_tick: bool = False):
        """
        实例化机器人
        Args:
            account: QQ账号
            readonly: 只读模式，跳过本地化文件写入（适用于测试环境）
            no_tick: 为 True 时不启动 tick_loop（供确定性自动化测试）
        """
        import core.command as command
        import module  # 加载各 module 子包以注册命令、本地化键等，需尽早 import
        from module.dice_hub import HubManager
        from adapter import ClientProxy
        self.account: str = account
        self.proxy: Optional[ClientProxy] = None
        self.data_path = os.path.join(BOT_DATA_PATH, account)

        self.fix_data()
        self.db = BotDatabase(self.account)
        self.hub_manager = HubManager(self)
        bot_config_path = os.path.join(self.data_path, "Config")
        os.makedirs(bot_config_path, exist_ok=True)
        self.loc_helper = LocalizationManager(bot_config_path, self.account)
        self.cfg_helper = ConfigManager(bot_config_path, self.account)

        self.command_dict: Dict[str, command.UserCommandBase] = {}

        self.tick_task: Optional[asyncio.Task] = None
        self.todo_tasks: Dict[Union[Callable, asyncio.Task], Dict] = {}
        self._no_tick: bool = no_tick

        # Some packaged runs may receive events before on_bot_connect completes.
        # Guard delay_init_command() so DB + per-command initialization always happen once.
        self._delay_init_lock = asyncio.Lock()
        self._delay_init_done: bool = False

        self.start_up(readonly=readonly)

    def set_client_proxy(self, proxy):
        from adapter import ClientProxy
        if isinstance(proxy, ClientProxy):
            self.proxy = proxy
        else:
            raise TypeError("Incorrect Client Proxy!")

    def start_up(self, readonly: bool = False):
        self.register_command()
        self.loc_helper.load_localization()  # 要在注册完命令后再读取本地化文件
        if not readonly:
            self.loc_helper.save_localization()  # 更新本地文件
        self.loc_helper.load_chat()
        if not readonly:
            self.loc_helper.save_chat()
        self.cfg_helper.load_config()
        self.cfg_helper.save_config()

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

        _meta_stat_row = await self.db.meta_stat.get("meta")
        if _meta_stat_row and _meta_stat_row.data:
            meta_stat = MetaStatInfo()
            try:
                meta_stat.deserialize(_meta_stat_row.data)
            except Exception:
                meta_stat = MetaStatInfo()
        else:
            meta_stat = MetaStatInfo()
        meta_stat.update(is_first_time=True)

        while True:
            loop_begin_time = loop.time()
            bot_commands: List[BotCommandBase] = []
            try:
                # tick each command
                for command in self.command_dict.values():
                    try:
                        bot_commands += command.tick()
                    except (AttributeError, TypeError, KeyError, RuntimeError):
                        dice_log(str(self.handle_exception(f"Tick: {command.readable_name} CODE110")[0]))

                if loop_begin_time - time_counter[0] > 60 * 5:  # 5分钟执行一次
                    # 更新在线时间并尝试每日更新
                    if meta_stat.update():
                        await self.tick_daily(bot_commands)
                    # 保存 meta_stat 到数据库
                    await self.db.meta_stat.upsert(MetaStat(key="meta", data=meta_stat.serialize()))
                    # 内存监控检查
                    await self._check_memory_and_handle()
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
            except (AttributeError, TypeError, KeyError, RuntimeError):
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
                except (AttributeError, TypeError, RuntimeError):
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
        except (AttributeError, TypeError, KeyError, RuntimeError):
            dice_log(str(self.handle_exception(f"Async Task: CODE112")[0]))

    async def _check_memory_and_handle(self) -> None:
        """内存监控：检查内存使用情况，必要时发送警告或触发重启"""
        if not PSUTIL_AVAILABLE:
            return
        try:
            enable = int(self.cfg_helper.get_config(CFG_MEMORY_MONITOR_ENABLE)[0])
            if not enable:
                return
        except (ValueError, TypeError, KeyError):
            return

        status = self.get_memory_status()
        if not status:
            return

        rss_mb = status["rss_mb"]
        percent = status["percent"]
        
        try:
            warn_pct = int(self.cfg_helper.get_config(CFG_MEMORY_WARN_PERCENT)[0])
            restart_pct = int(self.cfg_helper.get_config(CFG_MEMORY_RESTART_PERCENT)[0])
            restart_mb = int(self.cfg_helper.get_config(CFG_MEMORY_RESTART_MB)[0])
        except (ValueError, TypeError, KeyError):
            warn_pct, restart_pct, restart_mb = 80, 90, 2048

        if percent >= restart_pct or rss_mb >= restart_mb:
            msg = f"⚠️ 内存超限，正在自动重启\n当前: {rss_mb:.0f}MB ({percent:.1f}%)\n阈值: {restart_pct}% 或 {restart_mb}MB"
            dice_log(f"[MemoryMonitor] 内存超限，触发自动重启: {rss_mb:.0f}MB ({percent:.1f}%)")
            await self.send_msg_to_master(msg)
            await asyncio.sleep(2)
            self.reboot()
        elif percent >= warn_pct:
            msg = f"⚠️ 内存使用较高\n当前: {rss_mb:.0f}MB ({percent:.1f}%)\n警告阈值: {warn_pct}%\n建议关注运行状态"
            dice_log(f"[MemoryMonitor] 内存警告: {rss_mb:.0f}MB ({percent:.1f}%)")
            # 避免频繁警告，这里只记录日志，Master消息由用户手动查询
            # await self.send_msg_to_master(msg)

    def get_memory_status(self) -> Optional[Dict]:
        """获取当前内存使用状态，返回 None 表示无法获取"""
        if not PSUTIL_AVAILABLE:
            return None
        try:
            process = psutil.Process()
            mem_info = process.memory_info()
            rss_mb = mem_info.rss / (1024 * 1024)
            vm = psutil.virtual_memory()
            total_mb = vm.total / (1024 * 1024)
            percent = (rss_mb / total_mb) * 100
            return {
                "rss_mb": rss_mb,
                "total_mb": total_mb,
                "percent": percent,
                "system_percent": vm.percent,
            }
        except (AttributeError, TypeError, KeyError):
            return None

    async def tick_daily(self, bot_commands):
        # 更新用户统计
        user_stat_rows = await self.db.user_stat.list_all()
        user_updates = []
        for user_stat_row in user_stat_rows:
            if user_stat_row.data:
                user_stat = UserStatInfo()
                try:
                    user_stat.deserialize(user_stat_row.data)
                except Exception:
                    user_stat = UserStatInfo()
                user_stat.daily_update()
                user_updates.append(
                    UserStat(user_id=user_stat_row.user_id, data=user_stat.serialize())
                )
        await self.db.user_stat.upsert_many(user_updates)
        # 更新群聊统计
        group_stat_rows = await self.db.group_stat.list_all()
        group_updates = []
        for group_stat_row in group_stat_rows:
            if group_stat_row.data:
                group_stat = GroupStatInfo()
                try:
                    group_stat.deserialize(group_stat_row.data)
                except Exception:
                    group_stat = GroupStatInfo()
                group_stat.daily_update()
                group_updates.append(
                    GroupStat(group_id=group_stat_row.group_id, data=group_stat.serialize())
                )
        await self.db.group_stat.upsert_many(group_updates)

        # 尝试清理过期群聊和过期用户信息
        async def clear_expired_data():
            res = await self.clear_expired_data()
            return res

        self.register_task(clear_expired_data, timeout=3600)

        # 调用每个command的tick_daily方法
        for command in self.command_dict.values():
            try:
                bot_commands += command.tick_daily()
            except (AttributeError, TypeError, KeyError, RuntimeError):
                dice_log(str(self.handle_exception(f"Tick Daily: {command.readable_name} CODE111")[0]))
        # 给Master发送每日更新通知
        from core.localization import LOC_DAILY_UPDATE
        feedback = self.loc_helper.format_loc_text(LOC_DAILY_UPDATE)
        if feedback and feedback != "$":
            await self.send_msg_to_master(feedback)

        # 日志文件自动清理 (超过24小时的log文件删除)
        try:
            logs_dir = os.path.join(self.data_path, LOGS_SUBDIR)
            if os.path.isdir(logs_dir):
                now_ts = get_current_date_raw()
                for fname in os.listdir(logs_dir):
                    fpath = os.path.join(logs_dir, fname)
                    try:
                        stat = os.stat(fpath)
                        # 使用修改时间判断
                        if now_ts - stat.st_mtime > LOG_RETENTION_SECONDS:
                            if os.path.isfile(fpath):
                                os.remove(fpath)
                            elif os.path.isdir(fpath):
                                shutil.rmtree(fpath, ignore_errors=True)
                    except (OSError, PermissionError):
                        pass
        except (OSError, PermissionError):
            pass

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
        await self.db.close()

        if self.tick_task:
            self.tick_task.cancel()
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
        import platform
        
        python = sys.executable
        cwd = os.getcwd()
        
        # 记录重启信息用于调试
        dice_log(f"[Bot] [Reboot] Python: {python}")
        dice_log(f"[Bot] [Reboot] Args: {sys.argv}")
        dice_log(f"[Bot] [Reboot] CWD: {cwd}")
        
        if platform.system() == "Windows":
            # Windows: 使用 subprocess 启动新进程，然后退出当前进程
            import subprocess
            dice_log("[Bot] [Reboot] Windows 模式：启动新进程后退出")
            try:
                # 保留环境变量（包括虚拟环境的 PATH）
                env = os.environ.copy()
                subprocess.Popen(
                    [python] + sys.argv,
                    cwd=cwd,
                    env=env,
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
                )
            except (OSError, RuntimeError) as e:
                dice_log(f"[Bot] [Reboot] 启动新进程失败: {e}")
                # 回退到简单方式
                subprocess.Popen([python] + sys.argv, cwd=cwd)
            await asyncio.sleep(1)
            os._exit(0)
        else:
            # Linux/macOS: 使用 os.execl 替换当前进程
            dice_log("[Bot] [Reboot] Unix 模式：execl 替换进程")
            # 切换到原始工作目录
            os.chdir(cwd)
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
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
        loop.run_until_complete(self.delay_init_command())

    async def delay_init_command(self):
        """在载入本地化文本和配置等数据后调用"""
        async with self._delay_init_lock:
            if self._delay_init_done:
                return
            try:
                await self.db.connect()
            except Exception as exc:
                dice_log(f"[Migration] 数据库迁移失败，启动中断: {exc}")
                if self.proxy:
                    bc_list = self.handle_exception("启动前数据库迁移失败")
                    for bc in bc_list:
                        await self.proxy.process_bot_command(bc)
                raise

            # Hub 配置已迁移至数据库，启动时先加载到 HubManager 缓存。
            try:
                await self.hub_manager.load_config()
            except Exception as exc:
                dice_log(f"[DiceHub] 读取 Hub 配置失败，将使用内置默认值: {exc}")

            init_info: List[str] = []
            for command in self.command_dict.values():
                try:
                    init_info_cur = command.delay_init()
                    # 兼容某些命令在启动期需要异步初始化：delay_init 可能返回 awaitable
                    if asyncio.iscoroutine(init_info_cur):
                        init_info_cur = await init_info_cur
                    for i in range(len(init_info_cur)):
                        init_info_cur[i] = f"{command.__class__.readable_name}: {init_info_cur[i]}"
                    init_info += init_info_cur
                except (AttributeError, TypeError, RuntimeError):
                    if self.proxy:
                        bc_list = self.handle_exception(f"加载{command.__class__.__name__}失败")  # 报错不用中文名
                        for bc in bc_list:
                            await self.proxy.process_bot_command(bc)

            if self.proxy:
                from core.command import BotSendMsgCommand
                from core.localization import LOC_LOGIN_NOTICE
                from module.common.master_command import DC_CTRL

                # 检查是否开启了静默模式
                _ctrl_row = await self.db.bot_control.get("silent_startup")
                is_silent = _ctrl_row.value == "True" if _ctrl_row else False

                feedback = self.loc_helper.format_loc_text(LOC_LOGIN_NOTICE)
                if feedback and feedback != "$":
                    feedback_prefix = ""
                    for i in range(len(init_info)):
                        if init_info[i] and init_info[i] != "$":
                            feedback_prefix += init_info[i] + "\n"
                    feedback = f"{feedback_prefix}\n{feedback}"
                    dice_log(feedback)

                    # 如果开启了静默模式，跳过发送通知
                    if is_silent:
                        dice_log("[Bot] 静默模式已开启，跳过发送启动通知")
                    else:
                        # 给上次reboot的Admin或Master汇报
                        _rebooter_row = await self.db.bot_control.get("rebooter")
                        rebooter = _rebooter_row.value if _rebooter_row else ""
                        if rebooter != "":
                            await self.db.bot_control.upsert(BotControl(key="rebooter", value=""))
                            command = BotSendMsgCommand(self.account, feedback, [PrivateMessagePort(rebooter)])
                            await self.proxy.process_bot_command(command)
                        # 如果不存在reboot者，则给所有Master汇报
                        else:
                            for master in self.cfg_helper.get_config(CFG_MASTER):
                                command = BotSendMsgCommand(self.account, feedback, [PrivateMessagePort(master)])
                                await self.proxy.process_bot_command(command)
                else:
                    dice_log(init_info)

            if not self._no_tick and (self.tick_task is None or self.tick_task.done()):
                try:
                    asyncio.get_running_loop()
                    self.tick_task = asyncio.create_task(self.tick_loop())
                except RuntimeError:
                    pass

            self._delay_init_done = True

    # noinspection PyBroadException
    async def process_message(self, msg: str, meta: MessageMetaData) -> List:
        """处理消息"""
        from core.command import BotCommandBase, BotSendMsgCommand, BotSendForwardMsgCommand

        # Packaged runs may receive events before on_bot_connect completes.
        # Ensure DB + per-command delay_init have been executed once.
        if not self._delay_init_done:
            await self.delay_init_command()

        await self.update_nickname(meta.user_id, "origin", meta.nickname)

        msg = preprocess_msg(msg)  # 转换中文符号, 转换小写等等

        bot_commands: List[BotCommandBase] = []

        # 统计信息 —— 从 SQLite 读取，失败则创建默认值
        _meta_stat_row = await self.db.meta_stat.get("meta")
        if _meta_stat_row and _meta_stat_row.data:
            meta_stat = MetaStatInfo()
            try:
                meta_stat.deserialize(_meta_stat_row.data)
            except Exception:
                meta_stat = MetaStatInfo()
        else:
            meta_stat = MetaStatInfo()

        _user_stat_row = await self.db.user_stat.get(meta.user_id)
        if _user_stat_row and _user_stat_row.data:
            user_stat = UserStatInfo()
            try:
                user_stat.deserialize(_user_stat_row.data)
            except Exception:
                user_stat = UserStatInfo()
        else:
            user_stat = UserStatInfo()

        # 修改meta的permission参数
        # 4:骰主 3:骰管理 2:群主 1:群管理 0:普通人 -1:黑名单
        if meta.user_id in self.cfg_helper.get_config(CFG_MASTER):
            meta.permission = 4
        elif meta.user_id in self.cfg_helper.get_config(CFG_ADMIN):
            meta.permission = 3
        else:
            if meta.sender.role is not None:
                if meta.sender.role == "owner": # 群主 权限2
                    meta.permission = 2
                elif meta.sender.role == "admin": # 群管理 权限1
                    meta.permission = 1
                else: #elif meta.sender.role == "member": # 群员，或普通人
                    meta.permission = 0
        # 群内资料同步 —— 从 SQLite 读取
        if meta.group_id:
            _group_stat_row = await self.db.group_stat.get(meta.group_id)
            if _group_stat_row and _group_stat_row.data:
                group_stat = GroupStatInfo()
                try:
                    group_stat.deserialize(_group_stat_row.data)
                except Exception:
                    group_stat = GroupStatInfo()
            else:
                group_stat = GroupStatInfo()
        else:
            group_stat = GroupStatInfo()
        # 统计收到的消息数量
        meta_stat.msg.inc()
        group_stat.msg.inc()
        user_stat.msg.inc()

        # 处理分行指令
        command_split: str = self.cfg_helper.get_config(CFG_COMMAND_SPLIT)[0]
        msg_list = msg.split(command_split)
        msg_list = [m.strip() for m in msg_list]
        is_multi_command = len(msg_list) > 1

        # 遍历所有指令, 尝试处理消息
        for msg_cur in msg_list:
            for command in self.command_dict.values():
                # 判断是否能处理该条指令
                import inspect
                try:
                    if inspect.iscoroutinefunction(command.can_process_msg):
                        should_proc, should_pass, hint = await command.can_process_msg(msg_cur, meta)
                    else:
                        should_proc, should_pass, hint = command.can_process_msg(msg_cur, meta)
                except (AttributeError, TypeError, ValueError):
                    # 发现未处理的错误, 汇报给主Master
                    should_proc, should_pass, hint = False, False, None
                    info = f"{msg_list}中的{msg_cur}" if is_multi_command else msg
                    group_info = f"群:{meta.group_id}" if meta.group_id else "私聊"
                    bot_commands += self.handle_exception(f"来源:{info}\n用户:{meta.user_id} {group_info}出错位置:{command.readable_name}\n错误代码：CODE100")
                if not should_proc:
                    continue
                # 在非群聊中企图执行群聊指令, 回复一条提示
                if command.group_only and not meta.group_id:
                    feedback = self.loc_helper.format_loc_text(LOC_GROUP_ONLY_NOTICE)
                    bot_commands += [BotSendMsgCommand(self.account, feedback, [PrivateMessagePort(meta.user_id)])]
                    break
                # 无权限者/权限不足者企图使用一条需要权限的指令
                if meta.permission < command.permission_require:
                    # 骰管理及以上级别的指令 (permission_require >= 3) 对普通用户静默，避免暴露管理指令
                    if command.permission_require < 3:
                        feedback = self.loc_helper.format_loc_text(LOC_PERMISSION_DENIED_NOTICE)
                        bot_commands += [BotSendMsgCommand(self.account, feedback, [GroupMessagePort(meta.group_id) if meta.group_id else PrivateMessagePort(meta.user_id)])]
                    break
                # 执行指令
                # 注意: process_msg 是异步方法，需要使用 await 调用
                # 这允许命令内部使用异步数据库操作 (self.bot.db.xxx)
                res_commands = []
                try:
                    res_commands = await command.process_msg(msg_cur, meta, hint)
                    bot_commands += res_commands
                except (AttributeError, TypeError, ValueError, RuntimeError):
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

        # 将统计数据写回 SQLite
        try:
            await self.db.user_stat.upsert(UserStat(user_id=meta.user_id, data=user_stat.serialize()))
            await self.db.meta_stat.upsert(MetaStat(key="meta", data=meta_stat.serialize()))
            if meta.group_id:
                await self.db.group_stat.upsert(GroupStat(group_id=meta.group_id, data=group_stat.serialize()))
        except Exception as _exc:
            dice_log(f"[Stat] 写入统计 DB 失败: {_exc}")

        return bot_commands

    def process_request(self, data: RequestData) -> Optional[bool]:
        """处理请求"""
        if isinstance(data, FriendRequestData):
            passwords: List[str] = self.cfg_helper.get_config(CFG_FRIEND_TOKEN)
            passwords = [password.strip() for password in passwords if password.strip()]
            comment: str = data.comment.strip()
            return not passwords or comment in passwords
        elif isinstance(data, JoinGroupRequestData):
            should_allow: int = int(self.cfg_helper.get_config(CFG_GROUP_INVITE)[0])
            return should_allow == 1
        elif isinstance(data, InviteGroupRequestData):
            should_allow: int = int(self.cfg_helper.get_config(CFG_GROUP_INVITE)[0])
            return should_allow == 1
        return False

    async def process_notice(self, data: NoticeData) -> List:
        """处理提醒"""
        from core.command import BotCommandBase, BotSendMsgCommand
        from module.common import DC_ACTIVATE, DC_WELCOME, LOC_WELCOME_DEFAULT
        bot_commands: List[BotCommandBase] = []

        # Ensure DB + per-command init completed before reading/writing sqlite.
        if not self._delay_init_done:
            await self.delay_init_command()

        if isinstance(data, FriendAddNoticeData):
            feedback = self.loc_helper.format_loc_text(LOC_FRIEND_ADD_NOTICE)
            bot_commands += [BotSendMsgCommand(self.account, feedback, [PrivateMessagePort(data.user_id)])]
        elif isinstance(data, GroupIncreaseNoticeData):
            data: GroupIncreaseNoticeData = data
            if data.user_id != self.account:
                _activate_row = await self.db.group_activate.get(data.group_id)
                activate = _activate_row.active if _activate_row else True

                if activate:
                    _welcome_row = await self.db.group_welcome.get(data.group_id)
                    feedback = _welcome_row.welcome_msg if _welcome_row else "default"

                    if feedback == "default":
                        feedback = self.loc_helper.format_loc_text(LOC_WELCOME_DEFAULT)
                    
                    if feedback:
                        bot_commands += [BotSendMsgCommand(self.account, choice(feedback.split("|")), [GroupMessagePort(data.group_id)])]

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

    async def get_nickname(self, user_id: str, group_id: str = "") -> str:
        """
        获取用户昵称
        Args:
            user_id: 账号
            group_id: 群号, 为空代表默认
        """
        if not group_id:
            group_id = "default"

        _nick_row = await self.db.nickname.get(user_id, group_id)
        if _nick_row:
            return _nick_row.nickname
        _nick_row = await self.db.nickname.get(user_id, "default")
        if _nick_row:
            return _nick_row.nickname
        _nick_row = await self.db.nickname.get(user_id, "origin")
        if _nick_row:
            return _nick_row.nickname
        return NICKNAME_ERROR

    async def update_nickname(self, user_id: str, group_id: str = "", nickname: str = ""):
        """
        更新昵称
        Args:
            user_id: 账号
            group_id: 群号, 为空代表默认昵称, 为origin代表账号本身的名称, origin应该只在process_message时更新
            nickname: 昵称
        """
        if not group_id:
            group_id = "default"
        _nick_row = await self.db.nickname.get(user_id, group_id)
        if _nick_row is None or _nick_row.nickname != nickname:
            await self.db.nickname.upsert(UserNickname(user_id=user_id, group_id=group_id, nickname=nickname))

    async def update_group_info_all(self) -> List[GroupInfo]:
        if not self.proxy:
            return []
        group_info_list: List[GroupInfo] = await self.proxy.get_group_list()
        group_stat_rows = await self.db.group_stat.list_all()
        all_group_id = set(row.group_id for row in group_stat_rows)
        valid_group_id = set((info.group_id for info in group_info_list))
        for info in group_info_list:
            _row = await self.db.group_stat.get(info.group_id)
            if _row and _row.data:
                group_stat = GroupStatInfo()
                try:
                    group_stat.deserialize(_row.data)
                except Exception:
                    group_stat = GroupStatInfo()
            else:
                group_stat = GroupStatInfo()
            group_stat.meta.update(info.group_name, info.member_count, info.max_member_count)
            await self.db.group_stat.upsert(GroupStat(group_id=info.group_id, data=group_stat.serialize()))
        for group_id in all_group_id.difference(valid_group_id):
            _row = await self.db.group_stat.get(group_id)
            if _row and _row.data:
                group_stat = GroupStatInfo()
                try:
                    group_stat.deserialize(_row.data)
                except Exception:
                    group_stat = GroupStatInfo()
            else:
                group_stat = GroupStatInfo()
            group_stat.meta.member_count = -1
            group_stat.meta.max_member = -1
            await self.db.group_stat.upsert(GroupStat(group_id=group_id, data=group_stat.serialize()))

        return group_info_list

    def fix_data(self):
        pass

    async def clear_expired_data(self) -> List:
        from core.command import BotSendMsgCommand, BotDelayCommand, BotLeaveGroupCommand, BotCommandBase
        from module.character.dnd5e import DC_CHAR_DND, DC_CHAR_HP

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
        user_stat_rows = await self.db.user_stat.list_all()
        all_user_id: Set[str] = set(row.user_id for row in user_stat_rows)
        invalid_user_id = []
        for user_id in all_user_id:
            is_valid = False
            if user_id in white_list_user:
                continue
            _row = await self.db.user_stat.get(user_id)
            if not _row or not _row.data:
                invalid_user_id.append(user_id)
                continue
            user_stat = UserStatInfo()
            try:
                user_stat.deserialize(_row.data)
            except Exception:
                invalid_user_id.append(user_id)
                continue
            if user_stat.roll.times.total_val > 200:
                is_valid = True
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
            await self.db.user_stat.delete(user_id)

        # 清理过期群聊消息
        group_stat_rows = await self.db.group_stat.list_all()
        all_group_id: Set[str] = set(row.group_id for row in group_stat_rows)
        invalid_group_id = []
        warning_group_id = []
        for group_id in all_group_id:
            is_valid = False
            if group_id in white_list_group:
                continue
            _row = await self.db.group_stat.get(group_id)
            if not _row or not _row.data:
                invalid_group_id.append(group_id)
                continue
            group_stat = GroupStatInfo()
            try:
                group_stat.deserialize(_row.data)
            except Exception:
                invalid_group_id.append(group_id)
                continue
            for flag in group_stat.cmd.flag_dict.keys():
                flag_date = int_to_datetime(group_stat.cmd.flag_dict[flag].update_time)
                if cur_date - flag_date < datetime.timedelta(days=group_expire_day):
                    is_valid = True
                    break
            if not is_valid and group_stat.meta.warn_time < group_expire_time:
                is_valid = True
                group_stat.meta.warn_time += 1
                if group_stat.meta.member_count > 0:
                    result_commands.append(BotDelayCommand(self.account, seconds=random.random() * 10 + 2))
                    result_commands.append(BotSendMsgCommand(self.account, group_expire_warn, [GroupMessagePort(group_id)]))
                    warning_group_id.append(group_id)
                await self.db.group_stat.upsert(GroupStat(group_id=group_id, data=group_stat.serialize()))
            if not is_valid:
                invalid_group_id.append(group_id)
            index += 1
            if index % 500 == 0:
                await asyncio.sleep(0)
        for group_id in invalid_group_id:
            result_commands.append(BotDelayCommand(self.account, seconds=random.random() * 10 + 2))
            result_commands.append(BotLeaveGroupCommand(self.account, group_id))
            await self.db.group_stat.delete(group_id)

        # 给Master汇报清理情况
        if self.get_master_ids():
            master_id = self.get_master_ids()[0]
            result_commands.append(BotDelayCommand(self.account, seconds=random.random() * 10 + 2))
            feedback = f"检查{len(all_user_id)}个用户数据, {len(all_group_id)}个群聊数据.\n" \
                       f"清理{len(invalid_user_id)}个失效用户, {len(invalid_group_id)}个失效群聊({invalid_group_id}).\n" \
                       f"对{len(warning_group_id)}个即将失效的群聊发送提示消息."
            # 太长了别发给master了
            # result_commands.append(BotSendMsgCommand(self.account, feedback, [PrivateMessagePort(master_id)]))
        result_commands = list(reversed(result_commands))
        return result_commands
