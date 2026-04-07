"""
LLM 对话命令 - 提供 AI 对话功能
"""

from typing import List, Tuple, Any

from core.bot import Bot
from core.command.user_cmd import UserCommandBase, custom_user_command
from core.command.bot_cmd import BotSendMsgCommand
from core.communication import PrivateMessagePort, GroupMessagePort, MessageMetaData
from core.command.const import DPP_COMMAND_PRIORITY_DEFAULT, DPP_COMMAND_FLAG_FUN
from .client import SimpleLLMClient
from .memory import SimpleMemory
from utils.logger import dice_log


@custom_user_command("LLM对话", priority=DPP_COMMAND_PRIORITY_DEFAULT, flag=DPP_COMMAND_FLAG_FUN)
class LLMChatCommand(UserCommandBase):
    """LLM 对话命令处理器"""

    def __init__(self, bot: Bot):
        super().__init__(bot)
        self.client: SimpleLLMClient = None
        self.memory: SimpleMemory = None
        self.enabled: bool = False
        self.system_prompt: str = "你是一个 helpful 的助手，回答简洁。"
        self.timeout: int = 10
        self.max_input_length: int = 2000

    def delay_init(self) -> List[str]:
        """延迟初始化 LLM 客户端"""
        llm_cfg = self.bot.config.llm
        self.enabled = llm_cfg.enabled

        if not self.enabled:
            return ["LLM 模块已禁用"]

        try:
            api_key = llm_cfg.api_key
            if not api_key:
                self.enabled = False
                return ["LLM 模块初始化失败: 未配置 API Key"]

            self.client = SimpleLLMClient(
                api_key=api_key,
                base_url=llm_cfg.base_url,
                model=llm_cfg.model,
            )
            self.memory = SimpleMemory(max_size=llm_cfg.max_context)
            self.system_prompt = llm_cfg.personality
            self.timeout = llm_cfg.timeout

            return [f"LLM 模块已加载 (模型: {llm_cfg.model})"]
        except Exception as e:
            self.enabled = False
            dice_log(f"[LLM] 初始化失败: {e}")
            return [f"LLM 模块初始化失败: {e}"]

    async def can_process_msg(self, msg_str: str, meta: MessageMetaData) -> Tuple[bool, bool, Any]:
        """判断是否处理消息"""
        if not self.enabled or not self.client:
            # 即使未启用，也响应 .llm status 或单独的 .llm
            if msg_str.strip() == ".llm" or msg_str.strip().startswith(".llm "):
                return True, False, "status"
            return False, False, None

        # @bot 或 .llm 前缀触发
        if meta.to_me or msg_str.strip().startswith(".llm"):
            return True, False, None

        return False, False, None

    async def process_msg(self, msg_str: str, meta: MessageMetaData, hint: Any) -> List:
        """处理消息"""
        user_id = meta.user_id
        group_id = meta.group_id

        # 提取用户消息
        user_msg = msg_str.strip()
        if user_msg.startswith(".llm"):
            user_msg = user_msg[4:].strip()

        # 特殊命令
        if user_msg == "clear":
            self.memory.clear(user_id, group_id)
            response = "对话历史已清空"
        elif user_msg == "status" or hint == "status":
            # 显示 LLM 状态
            if not self.enabled or not self.client:
                response = "LLM 状态: 未启用\n原因: 未配置 API Key"
            else:
                history_count = len(self.memory.get_history(user_id, group_id))
                # 截断过长的 personality 显示
                personality_display = self.system_prompt[:50] + "..." if len(self.system_prompt) > 50 else self.system_prompt
                response = (
                    f"LLM 状态: 已启用\n"
                    f"模型: {self.client.model}\n"
                    f"人格: {personality_display}\n"
                    f"上下文: {history_count} 条\n"
                    f"超时: {self.timeout} 秒\n"
                    f"最大输入: {self.max_input_length} 字符"
                )
        elif not user_msg:
            # 单独的 .llm 命令显示状态
            if not self.enabled or not self.client:
                response = "LLM 状态: 未启用\n原因: 未配置 API Key"
            else:
                history_count = len(self.memory.get_history(user_id, group_id))
                # 截断过长的 personality 显示
                personality_display = self.system_prompt[:50] + "..." if len(self.system_prompt) > 50 else self.system_prompt
                response = (
                    f"LLM 状态: 已启用\n"
                    f"模型: {self.client.model}\n"
                    f"人格: {personality_display}\n"
                    f"上下文: {history_count} 条\n"
                    f"超时: {self.timeout} 秒\n"
                    f"最大输入: {self.max_input_length} 字符\n\n"
                    f"使用方法: .llm <消息> 或 @bot <消息>"
                )
        else:
            # 检查输入长度
            if len(user_msg) > self.max_input_length:
                response = f"输入过长（最大 {self.max_input_length} 字符）"
            else:
                # 获取或初始化历史
                history = self.memory.get_history(user_id, group_id)
                if not history:
                    # 首次对话，添加系统 prompt
                    history = [{"role": "system", "content": self.system_prompt}]

                # 添加用户消息到历史
                history.append({"role": "user", "content": user_msg})

                # 调用 LLM
                try:
                    response = await self.client.chat(history, timeout=self.timeout)

                    # 保存到记忆
                    self.memory.add_message(user_id, "user", user_msg, group_id)
                    self.memory.add_message(user_id, "assistant", response, group_id)
                except Exception as e:
                    dice_log(f"[LLM] 处理消息时出错: {e}")
                    response = "出错了，请稍后再试..."

        # 发送回复
        port = GroupMessagePort(group_id) if group_id else PrivateMessagePort(user_id)
        return [BotSendMsgCommand(self.bot.account, response, [port])]

    def get_help(self, keyword: str, meta: MessageMetaData) -> str:
        """获取帮助信息"""
        if keyword in ["llm", "ai", "聊天", "AI"]:
            return ".llm - 查看 LLM 状态\n.llm <消息> 或 @bot <消息> - 与 AI 对话\n.llm clear - 清空对话历史\n.llm status - 查看 LLM 状态"
        return ""

    def get_description(self) -> str:
        """获取命令描述"""
        return "AI 智能对话" if self.enabled else "AI 智能对话（已禁用）"
