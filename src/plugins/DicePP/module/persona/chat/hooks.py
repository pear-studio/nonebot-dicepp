"""对话生命周期钩子 — 所有钩子默认 no-op，按需覆盖

TODO: 任务二接入用，由 ChatSession 实例化并嵌入对话生命周期。
"""
from typing import List, Dict, Optional


class ChatHooks:
    """对话生命周期钩子"""

    async def on_before_chat(
        self, user_id: str, group_id: str, message: str
    ) -> Optional[str]:
        """返回非 None 则短路，直接作为回复返回；None 表示继续正常流程"""
        return None

    async def on_after_context_built(
        self, user_id: str, group_id: str, messages: List[Dict]
    ) -> List[Dict]:
        """修改或增强注入 LLM 的消息列表"""
        return messages

    async def on_before_send(
        self, user_id: str, group_id: str, content: str
    ) -> Optional[str]:
        """修改最终发送内容。返回 None 表示已处理（已发送），不再走默认发送流程"""
        return content

    async def on_error(
        self, user_id: str, group_id: str, error: Exception
    ) -> Optional[str]:
        """自定义错误回复，返回 None 使用默认错误消息"""
        return None
