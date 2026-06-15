import os
import asyncio
import datetime
import inspect
import random
import traceback
from typing import List, Optional, Dict, Callable, Set, Awaitable, Protocol, runtime_checkable
from random import choice

from utils.logger import logger, get_exception_info, configure_log_level
from utils.time import str_to_datetime, get_current_date_str, get_current_date_raw, int_to_datetime
from core.localization import LocalizationManager, LOC_GROUP_ONLY_NOTICE, LOC_PERMISSION_DENIED_NOTICE, LOC_FRIEND_ADD_NOTICE, LOC_GROUP_EXPIRE_WARNING
from core.config import Paths
from core.config.loader import ConfigLoader, ConfigValidationError
from core.config.pydantic_models import BotConfig
from core.bot.task_scheduler import TaskScheduler
from core.persona import PersonaLoader
from core.communication import MessageMetaData, MessagePort, PrivateMessagePort, GroupMessagePort, preprocess_msg
from core.communication import RequestData, FriendRequestData, JoinGroupRequestData, InviteGroupRequestData
from core.communication import NoticeData, FriendAddNoticeData, GroupIncreaseNoticeData
from core.communication import GroupInfo
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


@runtime_checkable
class PostSendHook(Protocol):
    """post_send_hook 回调签名"""
    async def __call__(
        self,
        group_id: str,
        user_id: str,
        role: str,
        type: str,
        content: str,
        display_name: str,
        msg_id: Optional[int] = None,
    ) -> None: ...


@runtime_checkable
class InboundMessageHook(Protocol):
    """入站消息记录 hook 回调签名"""
    async def __call__(
        self,
        user_id: str,
        group_id: str,
        role: str,
        type: str,
        content: str,
        display_name: str,
        raw_msg: str = "",
    ) -> None: ...


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
        self.data_path = str(Paths.bot_data_dir(account))

        Paths.ensure_dirs()
        self.fix_data()
        self.db = BotDatabase(self.account)
        self.hub_manager = HubManager(self)

        # New config system: ConfigLoader + PersonaLoader
        self._cfg_loader = ConfigLoader(account=account)
        self.config: BotConfig = self._cfg_loader.load()
        configure_log_level(self.config.log.level)
        self._persona_loader = PersonaLoader(self.config.persona_ai.character_path)

        # LocalizationManager now takes a PersonaLoader; no file paths needed
        self.loc_helper = LocalizationManager(persona_loader=self._persona_loader)

        self.command_dict: Dict[str, command.UserCommandBase] = {}

        self.tick_task: Optional[asyncio.Task] = None
        self.scheduler = TaskScheduler(error_handler=self.handle_exception)
        self._no_tick: bool = no_tick

        # 健康监控
        from module.bot_health.monitor import HealthMonitor
        hc = self.config.health_monitor
        self.health_monitor = HealthMonitor(
            account=self.account,
            heartbeat_timeout_seconds=hc.heartbeat_timeout_seconds,
            consecutive_fail_threshold=hc.consecutive_fail_threshold,
            failure_log_interval_seconds=hc.failure_log_interval_seconds,
        )

        # Some packaged runs may receive events before on_bot_connect completes.
        self._delay_init_lock = asyncio.Lock()
        self._delay_init_done: bool = False

        # 消息发送后跨模块通知 hook 列表
        # adapter 层发送消息后触发 hook，各模块通过注册 hook 实现日志记录等横切关注点。
        self._post_send_hooks: List[PostSendHook] = []

        # 消息入站记录 hook 列表
        self._inbound_message_hooks: List[InboundMessageHook] = []

        self.start_up(readonly=readonly)

    def set_client_proxy(self, proxy):
        from adapter import ClientProxy
        if isinstance(proxy, ClientProxy):
            self.proxy = proxy
        else:
            raise TypeError("Incorrect Client Proxy!")

    def add_post_send_hook(
        self,
        hook: PostSendHook,
    ) -> Callable[[], None]:
        """注册消息发送后跨模块通知 hook

        回调签名: (group_id, user_id, role, type, content, display_name, msg_id) -> Awaitable[None]
        返回注销函数，调用即可移除该 hook。
        """
        if hook not in self._post_send_hooks:
            self._post_send_hooks.append(hook)

        def unregister() -> None:
            if hook in self._post_send_hooks:
                self._post_send_hooks.remove(hook)

        return unregister

    def add_inbound_message_hook(
        self,
        hook: InboundMessageHook,
    ) -> Callable[[], None]:
        """注册入站消息记录 hook

        回调签名: (user_id, group_id, role, type, content, display_name) -> Awaitable[None]
        返回注销函数。
        """
        if hook not in self._inbound_message_hooks:
            self._inbound_message_hooks.append(hook)

        def unregister() -> None:
            if hook in self._inbound_message_hooks:
                self._inbound_message_hooks.remove(hook)

        return unregister

    def start_up(self, readonly: bool = False):
        self.register_command()
        # Apply persona overrides after commands have registered their loc keys
        self.loc_helper.set_persona(self.config.persona)

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
                    except Exception as _ex:
                        _type = type(_ex).__name__
                        logger.error(f"[TickLoop] 未预期异常 {_type}: {_ex}")
                        logger.error(traceback.format_exc())
                        bot_commands += self.handle_exception(f"Tick: {command.readable_name} CODE110 ({_type})")

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
                    self.scheduler.schedule(update_group_info, timeout=3600)
                    # 更新计时器
                    time_counter[1] = loop_begin_time

                if self.scheduler.pending:
                    free_time = max(loop_begin_time + 1 - loop.time(), 0.25)
                    bot_commands += await self.scheduler.process(free_time)

                if self.proxy:
                    for command in bot_commands:
                        await self.proxy.process_bot_command(command)
            except Exception as _ex:
                _type = type(_ex).__name__
                logger.error(f"[TickLoop] 未预期异常 {_type}: {_ex}")
                logger.error(traceback.format_exc())
                bot_commands += self.handle_exception(f"Tick Loop: CODE113 ({_type})")

            # 健康监控：周期性心跳超时检测
            self.health_monitor.check_heartbeat()

            # 最多每秒执行一次循环
            free_time = max(loop_begin_time + 1 - loop.time(), 0)
            await asyncio.sleep(free_time)

    async def _check_memory_and_handle(self) -> None:
        """内存监控：检查内存使用情况，必要时发送警告或触发重启"""
        if not PSUTIL_AVAILABLE:
            return
        if not self.config.memory_monitor.enable:
            return

        status = self.get_memory_status()
        if not status:
            return

        rss_mb = status["rss_mb"]
        percent = status["percent"]
        warn_pct = self.config.memory_monitor.warn_percent
        restart_pct = self.config.memory_monitor.restart_percent
        restart_mb = self.config.memory_monitor.restart_mb

        if percent >= restart_pct or rss_mb >= restart_mb:
            msg = f"⚠️ 内存超限，正在自动重启\n当前: {rss_mb:.0f}MB ({percent:.1f}%)\n阈值: {restart_pct}% 或 {restart_mb}MB"
            logger.error(f"[MemoryMonitor] 内存超限，触发自动重启: {rss_mb:.0f}MB ({percent:.1f}%)")
            await self.send_msg_to_master(msg)
            await asyncio.sleep(2)
            self.reboot()
        elif percent >= warn_pct:
            msg = f"⚠️ 内存使用较高\n当前: {rss_mb:.0f}MB ({percent:.1f}%)\n警告阈值: {warn_pct}%\n建议关注运行状态"
            logger.warning(f"[MemoryMonitor] 内存警告: {rss_mb:.0f}MB ({percent:.1f}%)")
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

        self.scheduler.schedule(clear_expired_data, timeout=3600)

        # 调用每个command的tick_daily方法
        for command in self.command_dict.values():
            try:
                daily_result = command.tick_daily()
                if inspect.isawaitable(daily_result):
                    daily_result = await daily_result
                bot_commands += daily_result
            except Exception as _ex:
                _type = type(_ex).__name__
                logger.error(f"[TickLoop] 未预期异常 {_type}: {_ex}")
                logger.error(traceback.format_exc())
                bot_commands += self.handle_exception(f"Tick Daily: {command.readable_name} CODE111 ({_type})")
        # 给Master发送每日更新通知（Persona 日报启用时跳过）
        # 检查 PersonaCommand 实例的实际运行状态，而非 config 静态值：
        # config.enabled=True 但 PersonaApp 初始化失败时，实例 enabled=False，
        # 此处应与实例状态同步，避免日报和旧通知双双缺失。
        from plugins.DicePP.module.persona.command import PersonaCommand
        persona_running = any(
            isinstance(cmd, PersonaCommand) and cmd.enabled
            for cmd in self.command_dict.values()
        )
        if not (persona_running and self.config.persona_ai.daily_report_enabled):
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
        shutdown_results = await asyncio.gather(
            *[command.shutdown() for command in self.command_dict.values()],
            return_exceptions=True,
        )
        for command, result in zip(self.command_dict.values(), shutdown_results):
            if isinstance(result, Exception):
                import traceback
                tb_str = traceback.format_exception(type(result), result, result.__traceback__)
                logger.error(
                    f"[Bot] 命令 {command.__class__.readable_name} shutdown 失败: {result}\n"
                    f"{''.join(tb_str)}"
                )

        await self.scheduler.shutdown()

        if self.tick_task:
            self.tick_task.cancel()
            await asyncio.gather(self.tick_task, return_exceptions=True)
            self.tick_task = None

        await self.db.close()
        # 注意如果保存时文件不存在会用当前值写入default, 如果在读取自定义设置后删掉文件再保存, 就会得到一个不是默认的default sheet
        # config is read-only at runtime; hot-reload is triggered via .reload command

    def reboot(self):
        """重启bot"""
        asyncio.create_task(self.reboot_async())

    async def reboot_async(self):
        logger.info("[Bot] [Reboot] 开始重启")
        await self.shutdown_async()
        import sys
        import platform
        
        python = sys.executable
        cwd = os.getcwd()
        
        # 记录重启信息用于调试
        logger.debug(f"[Bot] [Reboot] Python: {python}")
        logger.debug(f"[Bot] [Reboot] Args: {sys.argv}")
        logger.debug(f"[Bot] [Reboot] CWD: {cwd}")
        
        if platform.system() == "Windows":
            # Windows: 使用 subprocess 启动新进程，然后退出当前进程
            import subprocess
            logger.info("[Bot] [Reboot] Windows 模式：启动新进程后退出")
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
                logger.error(f"[Bot] [Reboot] 启动新进程失败: {e}")
                # 回退到简单方式
                subprocess.Popen([python] + sys.argv, cwd=cwd)
            await asyncio.sleep(1)
            os._exit(0)
        else:
            # Linux/macOS: 使用 os.execl 替换当前进程
            logger.info("[Bot] [Reboot] Unix 模式：execl 替换进程")
            # 切换到原始工作目录
            os.chdir(cwd)
            os.execl(python, python, *sys.argv)
        # self.start_up()
        # await self.delay_init_command()

    def register_command(self, registry=None):
        from core.command.user_cmd import CommandRegistry, DEFAULT_REGISTRY
        if registry is None:
            registry = DEFAULT_REGISTRY
        for command_cls in registry.get_sorted_commands():
            self.command_dict[command_cls.__name__] = command_cls(bot=self)

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
                logger.error(f"[Migration] 数据库迁移失败，启动中断: {exc}")
                if self.proxy:
                    bc_list = self.handle_exception("启动前数据库迁移失败")
                    for bc in bc_list:
                        await self.proxy.process_bot_command(bc)
                raise

            # Hub 配置已迁移至数据库，启动时先加载到 HubManager 缓存。
            try:
                await self.hub_manager.load_config()
            except Exception as exc:
                logger.warning(f"[DiceHub] 读取 Hub 配置失败，将使用内置默认值: {exc}")

            # 注册日志记录 hook，消除 adapter -> log_command 反向导入
            from module.common.log_command import register_log_hooks
            register_log_hooks(self)

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
                    logger.info(feedback)

                    # 如果开启了静默模式，跳过发送通知
                    if is_silent:
                        logger.info("[Bot] 静默模式已开启，跳过发送启动通知")
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
                            for master in self.config.master:
                                command = BotSendMsgCommand(self.account, feedback, [PrivateMessagePort(master)])
                                await self.proxy.process_bot_command(command)
                else:
                    logger.info(init_info)

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
        if meta.user_id in self.config.master:
            meta.permission = 4
        elif meta.user_id in self.config.admin:
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
        group_stat.msg.inc()
        user_stat.msg.inc()

        # 处理分行指令
        command_split: str = self.config.command_split
        msg_list = msg.split(command_split)
        msg_list = [m.strip() for m in msg_list]
        is_multi_command = len(msg_list) > 1

        # 遍历所有指令, 尝试处理消息
        msg_type_default = "ambient"  # 兜底：不匹配任何命令时使用
        for msg_cur in msg_list:
            recorded = False
            for command in self.command_dict.values():
                # 判断是否能处理该条指令
                import inspect
                try:
                    if inspect.iscoroutinefunction(command.can_process_msg):
                        should_proc, should_pass, hint = await command.can_process_msg(msg_cur, meta)
                    else:
                        should_proc, should_pass, hint = command.can_process_msg(msg_cur, meta)
                except Exception as e:
                    # 抓全所有异常类型（原限定 3 种会导致 KeyError/OSError/asyncio.TimeoutError
                    # 等逃到 nonebot 事件循环无任何日志，与"沉默"行为吻合）。
                    # 与 process_msg:686 同步扩展。
                    should_proc, should_pass, hint = False, False, None
                    info = f"{msg_list}中的{msg_cur}" if is_multi_command else msg
                    group_info = f"群:{meta.group_id}" if meta.group_id else "私聊"
                    logger.error(f"[Bot] can_process_msg 未处理异常: {type(e).__name__}: {e}")
                    bot_commands += self.handle_exception(f"来源:{info}\n用户:{meta.user_id} {group_info}出错位置:{command.readable_name}\n错误代码：CODE100")
                if not should_proc:
                    continue
                # 入站记录（逐 msg_cur 去重）
                if not recorded and self._inbound_message_hooks:
                    display_name = meta.sender.card or meta.sender.nickname or meta.nickname or meta.user_id
                    msg_type = getattr(command, "message_type", None)
                    msg_type_val = msg_type.value if hasattr(msg_type, "value") else str(msg_type or msg_type_default)
                    for hook in self._inbound_message_hooks:
                        try:
                            await hook(
                                user_id=meta.user_id,
                                group_id=meta.group_id or "",
                                role="user",
                                type=msg_type_val,
                                content=msg_cur,
                                display_name=display_name,
                                raw_msg=meta.raw_msg,
                            )
                        except Exception as e:
                            logger.warning(f"[InboundHook] 记录失败: {e}")
                    recorded = True
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
                except Exception as e:
                    # 抓全所有异常类型（原限定 4 种会导致 KeyError/OSError/asyncio.TimeoutError
                    # 等逃到 nonebot 事件循环无任何日志，与"沉默"行为吻合）
                    info = f"{msg_list}中的{msg_cur}" if is_multi_command else msg
                    group_info = f"群:{meta.group_id}" if meta.group_id else "私聊"
                    logger.error(f"[Bot] 未处理异常上报 master: {type(e).__name__}: {e}")
                    bot_commands += self.handle_exception(f"来源:{info}\n用户:{meta.user_id} {group_info} CODE101")

                # 统计处理的指令情况
                if command.flag and res_commands:
                    user_stat.cmd.record(command)
                    group_stat.cmd.record(command)

                if not should_pass:  # 已经处理过, 不需要再传递给后面的指令
                    break

            # 未匹配任何命令的消息：以 ambient 类型记录（不参与 persona 上下文）
            if not recorded and self._inbound_message_hooks:
                display_name = meta.sender.card or meta.sender.nickname or meta.nickname or meta.user_id
                for hook in self._inbound_message_hooks:
                    try:
                        await hook(
                            user_id=meta.user_id,
                            group_id=meta.group_id or "",
                            role="user",
                            type=msg_type_default,
                            content=msg_cur,
                            display_name=display_name,
                            raw_msg=meta.raw_msg,
                        )
                    except Exception as e:
                        logger.warning(f"[InboundHook] 记录失败: {e}")

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
            if meta.group_id:
                await self.db.group_stat.upsert(GroupStat(group_id=meta.group_id, data=group_stat.serialize()))
        except Exception as _exc:
            logger.error(f"[Stat] 写入统计 DB 失败: {_exc}")

        return bot_commands

    def process_request(self, data: RequestData) -> Optional[bool]:
        """处理请求"""
        if isinstance(data, FriendRequestData):
            passwords = [t.strip() for t in self.config.friend_token if t.strip()]
            comment: str = data.comment.strip()
            return not passwords or comment in passwords
        elif isinstance(data, JoinGroupRequestData):
            return self.config.group_invite
        elif isinstance(data, InviteGroupRequestData):
            return self.config.group_invite
        return False

    async def process_notice(self, data: NoticeData) -> List:
        """处理提醒"""
        from core.command import BotCommandBase, BotSendMsgCommand
        from module.common import LOC_WELCOME_DEFAULT
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
        master_list = self.config.master
        if master_list:
            return [BotSendMsgCommand(self.account, feedback, [PrivateMessagePort(master_list[0])])]
        else:
            return []

    def get_master_ids(self) -> List[str]:
        return self.config.master

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
        is_data_expire = self.config.data_expire
        user_expire_day = self.config.user_expire_day
        group_expire_day = self.config.group_expire_day
        group_expire_time = self.config.group_expire_warning_time
        group_expire_warn = self.loc_helper.format_loc_text(LOC_GROUP_EXPIRE_WARNING)
        if not is_data_expire:
            return []
        result_commands: List[BotCommandBase] = []
        index = 0

        white_list_group: List[str] = self.config.white_list_group
        white_list_user: List[str] = self.config.white_list_user

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
