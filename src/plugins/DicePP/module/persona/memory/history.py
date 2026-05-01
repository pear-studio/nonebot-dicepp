"""历史管理 — 只做 CRUD，无业务逻辑

私聊 char-based 截断、群聊 token-based 截断等业务逻辑在 chat/context.py 的 ContextBuilder 中处理。

TODO: 任务二接入用，orchestrator 中的历史管理逻辑将迁移到 HistoryManager。
"""
from typing import List, Dict, Optional, Tuple
import logging

from ..data.store import PersonaDataStore
from ..data.models import GroupConversation

logger = logging.getLogger("persona.history")


class HistoryManager:
    """历史记录管理器 — 薄封装层"""

    def __init__(self, data_store: PersonaDataStore):
        self.data_store = data_store

    async def get_recent_messages(
        self, user_id: str, group_id: str, limit: int = 15
    ) -> List[Dict]:
        """获取近期消息（原始格式，不做截断）"""
        history = await self.data_store.get_recent_messages(user_id, group_id, limit=limit)
        return [
            {"role": msg.role, "content": msg.content, "speaker_name": "你" if msg.role == "user" else "我"}
            for msg in history
        ]

    async def get_group_conversations(
        self, group_id: str, limit: Optional[int] = None
    ) -> List[GroupConversation]:
        """获取群聊历史（原始格式，不做截断）"""
        return await self.data_store.get_group_conversations(group_id, limit=limit)

    async def append_message(
        self, user_id: str, group_id: str, role: str, content: str
    ) -> None:
        """追加单条消息"""
        await self.data_store.append_message(user_id, group_id, role, content)

    async def append_system(
        self, user_id: str, group_id: str, content: str
    ) -> None:
        """追加系统消息"""
        await self.data_store.append_message(user_id, group_id, "system", content)

    async def clear(self, user_id: str, group_id: str) -> None:
        """清空对话历史"""
        await self.data_store.clear_messages(user_id, group_id)
