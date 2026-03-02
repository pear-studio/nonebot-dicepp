"""
命令模板, 复制到新创建的文件里修改
"""

from typing import List, Tuple, Any
import os
import asyncio

from core.bot import Bot
from core.command.const import *
from core.command import UserCommandBase, custom_user_command
from core.command import BotCommandBase, BotSendMsgCommand
from core.communication import MessageMetaData, PrivateMessagePort, GroupMessagePort
from core.config import CFG_MASTER, CFG_ADMIN
from core.data import custom_data_chunk, DataChunkBase

LOC_REBOOT = "master_reboot"
LOC_SEND_MASTER = "master_send_to_master"
LOC_SEND_TARGET = "master_send_to_target"
LOC_LOG_CLEAN = "master_log_clean"
LOC_LOG_CLEAN_DONE = "master_log_clean_done"
LOC_LOG_STATUS_DONE = "master_log_status_done"
LOC_SILENT_ON = "master_silent_on"
LOC_SILENT_OFF = "master_silent_off"
LOC_SILENT_STATUS = "master_silent_status"

DC_CTRL = "master_control"

@custom_data_chunk(identifier=DC_CTRL,
                   include_json_object=True)
class _(DataChunkBase):
    def __init__(self):
        super().__init__()

@custom_user_command(readable_name="Master指令", priority=DPP_COMMAND_PRIORITY_MASTER,flag=DPP_COMMAND_FLAG_MANAGE,
                     permission_require=3 # 限定骰管理使用
                     )
class MasterCommand(UserCommandBase):
    """
    Master指令
    包括: reboot, send
    """

    def __init__(self, bot: Bot):
        super().__init__(bot)
        bot.loc_helper.register_loc_text(LOC_REBOOT, "重启已完毕。", "重启完成")
        bot.loc_helper.register_loc_text(LOC_SEND_MASTER,
                                         "发送消息: {msg} 至 {id} (类型:{type})",
                                         "用.m send指令发送消息时给Master的回复")
        bot.loc_helper.register_loc_text(LOC_SEND_TARGET, "自Master: {msg}", "用.m send指令发送消息时给目标的回复")
        bot.loc_helper.register_loc_text(LOC_LOG_CLEAN, "开始清理日志文件...", "Master清理日志时开始提示")
        bot.loc_helper.register_loc_text(LOC_LOG_CLEAN_DONE, "日志清理完成，共删除 {count} 个文件。", "Master清理日志完成提示")
        bot.loc_helper.register_loc_text(LOC_LOG_STATUS_DONE, "日志状态：文件 {count} 个，总计 {size_kb} KB。最近文件：\n{recent}", "Master查看日志状态")
        bot.loc_helper.register_loc_text(LOC_SILENT_ON, "已开启静默模式，启动时将不再发送通知给管理员。", "启用静默模式提示")
        bot.loc_helper.register_loc_text(LOC_SILENT_OFF, "已关闭静默模式，启动时将正常发送通知给管理员。", "关闭静默模式提示")
        bot.loc_helper.register_loc_text(LOC_SILENT_STATUS, "当前静默模式: {status}", "静默模式状态查询")

    def can_process_msg(self, msg_str: str, meta: MessageMetaData) -> Tuple[bool, bool, Any]:
        should_proc: bool = False
        should_pass: bool = False
        
        arg_str: str = ""
        if msg_str.startswith(".m"):
            should_proc = True
            arg_str = msg_str[2:].strip()
        elif msg_str.startswith(".master"):
            should_proc = True
            arg_str = msg_str[7:].strip()
        return should_proc, should_pass, arg_str

    def process_msg(self, msg_str: str, meta: MessageMetaData, hint: Any) -> List[BotCommandBase]:
        port = GroupMessagePort(meta.group_id) if meta.group_id else PrivateMessagePort(meta.user_id)
        # 解析语句
        arg_str: str = hint
        feedback: str
        command_list: List[BotCommandBase] = []

        if arg_str == "reboot" or arg_str == "reboot now":
            # 立即重启
            self.bot.data_manager.set_data(DC_CTRL, ["rebooter"], meta.user_id)
            try:
                self.bot.reboot()
                feedback = self.format_loc(LOC_REBOOT)
            except Exception:
                return self.bot.handle_exception("重启时出现错误")
        elif arg_str == "reboot info":
            # 显示重启相关信息（用于调试虚拟环境问题）
            import sys
            import platform
            info_lines = [
                "📋 重启环境信息",
                f"Python: {sys.executable}",
                f"版本: {sys.version.split()[0]}",
                f"平台: {platform.system()} {platform.release()}",
                f"工作目录: {os.getcwd()}",
                f"启动参数: {' '.join(sys.argv)}",
            ]
            # 检测虚拟环境
            venv = os.environ.get("VIRTUAL_ENV")
            if venv:
                info_lines.append(f"虚拟环境: {venv}")
            else:
                info_lines.append("虚拟环境: (未检测到)")
            feedback = "\n".join(info_lines)
        elif arg_str.startswith("reboot delay"):
            # 延迟重启: .m reboot delay 60
            parts = arg_str.split()
            delay_sec = 60
            if len(parts) >= 3:
                try:
                    delay_sec = int(parts[2])
                except ValueError:
                    feedback = "延迟秒数必须为整数"
                    command_list.append(BotSendMsgCommand(self.bot.account, feedback, [port]))
                    return command_list
            
            self.bot.data_manager.set_data(DC_CTRL, ["rebooter"], meta.user_id)
            
            async def delayed_reboot():
                from core.command import BotSendMsgCommand
                # 发送倒计时提醒
                await self.bot.send_msg_to_master(f"⏰ 骰娘将在 {delay_sec} 秒后重启...")
                await asyncio.sleep(delay_sec)
                self.bot.reboot()
                return []
            
            import asyncio
            self.bot.register_task(delayed_reboot, timeout=delay_sec + 30)
            feedback = f"已安排延迟重启，将在 {delay_sec} 秒后执行"
        elif arg_str.startswith("send"):
            arg_list = arg_str[4:].split(":", 2)
            if len(arg_list) == 3:
                target_type, target, msg = (arg.strip() for arg in arg_list)
                if target_type in ["user", "group"]:
                    feedback = self.format_loc(LOC_SEND_MASTER, msg=msg, id=target, type=target_type)
                    target_port = PrivateMessagePort(target) if target_type == "user" else GroupMessagePort(target)
                    command_list.append(BotSendMsgCommand(self.bot.account, msg, [target_port]))
                else:
                    feedback = "目标必须为user或group"
            else:
                feedback = f"非法输入\n使用方法: {self.get_help('m send', meta)}"
        elif arg_str == "update":
            async def async_task():
                update_group_result = await self.bot.update_group_info_all()
                update_feedback = f"已更新{len(update_group_result)}条群信息:"
                update_group_result = list(sorted(update_group_result, key=lambda x: -x.member_count))[:50]
                for group_info in update_group_result:
                    update_feedback += f"\n{group_info.group_name}({group_info.group_id}): 群成员{group_info.member_count}/{group_info.max_member_count}"
                return [BotSendMsgCommand(self.bot.account, update_feedback, [port])]

            self.bot.register_task(async_task, timeout=60, timeout_callback=lambda: [BotSendMsgCommand(self.bot.account, "更新超时!", [port])])
            feedback = "更新开始..."
        elif arg_str == "clean":
            async def clear_expired_data():
                res = await self.bot.clear_expired_data()
                return res

            self.bot.register_task(clear_expired_data, timeout=3600)
            feedback = "清理开始..."
        elif arg_str == "debug-tick":
            feedback = f"异步任务状态: {self.bot.tick_task.get_name()} Done:{self.bot.tick_task.done()} Cancelled:{self.bot.tick_task.cancelled()}\n" \
                       f"{self.bot.tick_task}"
        elif arg_str == "redo-tick":
            import asyncio
            self.bot.tick_task = asyncio.create_task(self.bot.tick_loop())
            self.bot.todo_tasks = {}
            feedback = "Redo tick finish!"
        elif arg_str == "log-clean":
            # 立即删除本Bot data_path/logs 下所有文件
            import shutil
            logs_dir = os.path.join(self.bot.data_path, "logs")
            removed = 0
            if os.path.isdir(logs_dir):
                for name in os.listdir(logs_dir):
                    path = os.path.join(logs_dir, name)
                    try:
                        if os.path.isfile(path):
                            os.remove(path)
                            removed += 1
                        else:
                            shutil.rmtree(path, ignore_errors=True)
                            removed += 1
                    except Exception:
                        pass
            feedback = self.format_loc(LOC_LOG_CLEAN_DONE, count=removed)
        elif arg_str.startswith("log"):
            # 支持格式: .m log status
            parts = arg_str.split()
            if len(parts) >= 2 and parts[1] == "status":
                import time
                logs_dir = os.path.join(self.bot.data_path, "logs")
                files_info = []
                total_size = 0
                if os.path.isdir(logs_dir):
                    for name in os.listdir(logs_dir):
                        path = os.path.join(logs_dir, name)
                        try:
                            if os.path.isfile(path):
                                stat = os.stat(path)
                                total_size += stat.st_size
                                files_info.append((name, stat.st_mtime, stat.st_size))
                        except Exception:
                            pass
                files_info.sort(key=lambda x: -x[1])
                recent_lines = []
                for item in files_info[:5]:
                    age_sec = int(time.time() - item[1])
                    recent_lines.append(f"{item[0]} ({age_sec}s前, {int(item[2]/1024)}KB)")
                recent_txt = "\n".join(recent_lines) if recent_lines else "(无)"
                feedback = self.format_loc(LOC_LOG_STATUS_DONE, count=len(files_info), size_kb=int(total_size/1024), recent=recent_txt)
            else:
                feedback = "未知log子命令，可用: log status | log-clean"
        elif arg_str == "memory" or arg_str == "mem":
            # 内存状态查询
            status = self.bot.get_memory_status()
            if status:
                from core.config import CFG_MEMORY_WARN_PERCENT, CFG_MEMORY_RESTART_PERCENT, CFG_MEMORY_RESTART_MB
                try:
                    warn_pct = int(self.bot.cfg_helper.get_config(CFG_MEMORY_WARN_PERCENT)[0])
                    restart_pct = int(self.bot.cfg_helper.get_config(CFG_MEMORY_RESTART_PERCENT)[0])
                    restart_mb = int(self.bot.cfg_helper.get_config(CFG_MEMORY_RESTART_MB)[0])
                except Exception:
                    warn_pct, restart_pct, restart_mb = 80, 90, 2048
                feedback = (
                    f"📊 内存状态\n"
                    f"当前占用: {status['rss_mb']:.1f} MB ({status['percent']:.1f}%)\n"
                    f"系统总内存: {status['total_mb']:.0f} MB\n"
                    f"系统已用: {status['system_percent']:.1f}%\n"
                    f"---\n"
                    f"警告阈值: {warn_pct}%\n"
                    f"重启阈值: {restart_pct}% 或 {restart_mb}MB"
                )
            else:
                feedback = "无法获取内存信息，可能未安装 psutil"
        elif arg_str == "silent" or arg_str == "silent status":
            # 查询静默模式状态
            is_silent = self.bot.data_manager.get_data(DC_CTRL, ["silent_startup"], False)
            status_text = "开启" if is_silent else "关闭"
            feedback = self.format_loc(LOC_SILENT_STATUS, status=status_text)
        elif arg_str == "silent on":
            # 开启静默模式
            self.bot.data_manager.set_data(DC_CTRL, ["silent_startup"], True)
            feedback = self.format_loc(LOC_SILENT_ON)
        elif arg_str == "silent off":
            # 关闭静默模式
            self.bot.data_manager.set_data(DC_CTRL, ["silent_startup"], False)
            feedback = self.format_loc(LOC_SILENT_OFF)
        else:
            feedback = self.get_help("m", meta)

        command_list.append(BotSendMsgCommand(self.bot.account, feedback, [port]))
        return command_list

    def get_help(self, keyword: str, meta: MessageMetaData) -> str:
        if keyword == "m":  # help后的接着的内容
         return ".m reboot 立即重启骰娘\n" \
             ".m reboot info 查看重启环境信息\n" \
             ".m reboot delay <秒> 延迟重启\n" \
             ".m send 命令骰娘发送信息\n" \
             ".m memory 查看内存状态\n" \
             ".m log-clean 清空日志目录\n" \
             ".m log status 查看日志状态\n" \
             ".m silent on/off 开启/关闭静默模式（启动时不发送通知）"
        if keyword.startswith("m"):
            if keyword.endswith("reboot"):
                return ".m reboot 立即重启\n.m reboot info 查看环境\n.m reboot delay 60 延迟60秒重启"
            elif keyword.endswith("send"):
                return ".m send [user/group]:[账号/群号]:[消息内容]"
        return ""

    def get_description(self) -> str:
        return ".m Master才能使用的指令"  # help指令中返回的内容
