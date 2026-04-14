"""Bot 运行包装器 - 管理 Bot 实例、捕获输出、控制骰子"""

import asyncio
from pathlib import Path
from typing import List, Optional, Dict, Any

from ..core.bot import Bot
from ..core.communication import MessageMetaData, MessageSender, GroupInfo, GroupMemberInfo
from ..adapter import ClientProxy
from ..core.command import BotCommandBase, BotSendMsgCommand
from ..utils.sequence_runtime import SequenceRuntime
# 注意：必须使用裸绝对导入 `module.roll.karma_runtime`，
# 因为 Bot 内部大量使用该路径导入此模块。
# 若使用相对导入 (`..module.roll.karma_runtime`)，会导致
# `sys.modules` 中出现两个副本，ContextVar 的读写将分离，
# 从而使 `--dice` 序列控制完全失效。
from module.roll.karma_runtime import set_runtime, reset_runtime


class CaptureProxy(ClientProxy):
    """捕获 Bot 输出，而不是真的发送到 QQ"""

    def __init__(self):
        super().__init__()
        self.commands: List[BotCommandBase] = []

    async def get_group_list(self) -> List[GroupInfo]:
        """返回空群组列表（shell 模式下无实际群组）"""
        return []

    async def get_group_info(self, group_id: str) -> GroupInfo:
        """返回虚拟群组信息"""
        return GroupInfo(group_id=group_id or "test_group")

    async def get_group_member_list(self, group_id: str) -> List[GroupMemberInfo]:
        """返回空成员列表"""
        return []

    async def get_group_member_info(self, group_id: str, user_id: str) -> GroupMemberInfo:
        """返回虚拟成员信息"""
        return GroupMemberInfo(group_id=group_id or "test_group", user_id=user_id)

    async def process_bot_command(self, command: BotCommandBase):
        """捕获单个命令"""
        self.commands.append(command)

    async def process_bot_command_list(self, command_list: List[BotCommandBase]):
        """捕获命令列表"""
        self.commands.extend(command_list)

    def get_display_text(self) -> str:
        """将捕获的命令转换为可读的文本输出"""
        lines = []
        for cmd in self.commands:
            if isinstance(cmd, BotSendMsgCommand):
                lines.append(cmd.msg)
        return "\n".join(lines) if lines else "(no output)"

    def clear(self):
        """清空捕获的命令"""
        self.commands.clear()

    def to_dict_list(self) -> List[Dict[str, Any]]:
        """将命令转换为字典列表（用于 JSON 输出）"""
        result = []
        for cmd in self.commands:
            if isinstance(cmd, BotSendMsgCommand):
                targets = []
                for t in cmd.targets:
                    target_info = {"type": t.__class__.__name__}
                    # 提取常见属性
                    if hasattr(t, "group_id"):
                        target_info["group_id"] = t.group_id
                    if hasattr(t, "user_id"):
                        target_info["user_id"] = t.user_id
                    targets.append(target_info)

                result.append({
                    "type": "send_msg",
                    "msg": cmd.msg,
                    "targets": targets,
                })
            else:
                result.append({
                    "type": cmd.__class__.__name__,
                })
        return result


class BotRunner:
    """管理单个 Bot 实例的生命周期和交互"""

    def __init__(self, session_dir: Path):
        self.session_dir = session_dir
        self.bot: Optional[Bot] = None
        self.proxy = CaptureProxy()
        self._started = False

    async def start(self) -> None:
        """启动 Bot 实例"""
        if self._started:
            return

        # 设置环境变量，让 Bot 使用 session_dir 作为数据目录
        import os
        original_data_dir = os.environ.get("DICEPP_APP_DIR")
        os.environ["DICEPP_APP_DIR"] = str(self.session_dir)

        try:
            # 创建 Bot 实例
            account = f"shell_{self.session_dir.name}"
            self.bot = Bot(account=account, no_tick=True)

            # 配置
            self.bot.config.master = ["shell_master"]
            self.bot.set_client_proxy(self.proxy)

            # 初始化
            await self.bot.delay_init_command()

            self._started = True
        finally:
            # 恢复环境变量
            if original_data_dir is not None:
                os.environ["DICEPP_APP_DIR"] = original_data_dir
            elif "DICEPP_APP_DIR" in os.environ:
                del os.environ["DICEPP_APP_DIR"]

    async def stop(self) -> None:
        """停止 Bot 实例"""
        if self.bot and self._started:
            await self.bot.shutdown_async()
            self.bot = None
            self._started = False

    async def send(
        self,
        user_id: str,
        nickname: str,
        msg: str,
        group_id: str = "",
        dice_sequence: Optional[List[int]] = None,
    ) -> Dict[str, Any]:
        """发送消息到 Bot

        Args:
            user_id: 用户ID
            nickname: 用户昵称
            msg: 消息内容
            group_id: 群组ID（空字符串表示私聊）
            dice_sequence: 可选的骰子序列

        Returns:
            包含输出文本和命令信息的字典
        """
        if not self._started or not self.bot:
            raise RuntimeError("Bot not started. Call start() first.")

        # 清空之前的输出
        self.proxy.clear()

        # 设置骰子序列
        token = None
        runtime = None
        if dice_sequence:
            runtime = SequenceRuntime(dice_sequence)
            token = set_runtime(runtime)

        try:
            # 构造消息元数据
            meta = MessageMetaData(
                plain_msg=msg,
                raw_msg=msg,
                sender=MessageSender(user_id, nickname),
                group_id=group_id,
                to_me=False,
            )

            # 处理消息
            commands = await self.bot.process_message(msg, meta)

            # 收集结果
            result = {
                "text": self.proxy.get_display_text(),
                "commands": self.proxy.to_dict_list(),
                "dice_consumed": runtime.get_consumed_count() if runtime else 0,
                "raw_command_count": len(commands),
            }

            return result

        finally:
            # 恢复骰子运行时
            if token is not None:
                reset_runtime(token)
