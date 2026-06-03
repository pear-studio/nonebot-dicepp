"""ResponseHandler — 封装回复持久化与 MessagePort 发送"""

from typing import Optional
from utils.logger import logger

from ..data.store import PersonaDataStore
from ..data.models import MessageType
from ..gateway.port import MessagePort


class ResponseHandler:
    """处理回复的持久化（入库）和发送（MessagePort）

    公开属性:
        port: MessagePort 实例（可能为 None），供 ToolContext 等外部使用
    """

    def __init__(
        self,
        store: PersonaDataStore,
        port: Optional[MessagePort] = None,
    ):
        self._store = store
        self.port = port

    async def persist(
        self,
        user_id: str,
        group_id: str,
        content: str,
        display_name: str = "我",
    ) -> int:
        """持久化 assistant 消息到 message_stream 表

        - 群聊: effective_user_id = "assistant"
        - 私聊: effective_user_id = user_id

        Returns:
            入库消息的 msg_id
        """
        effective_user_id = "assistant" if group_id else user_id
        msg_id = await self._store.add_message_stream(
            user_id=effective_user_id,
            group_id=group_id or "",
            role="assistant",
            type=MessageType.CHAT,
            content=content,
            display_name=display_name,
        )
        return msg_id

    async def send(
        self,
        user_id: str,
        group_id: str,
        content: str,
        msg_id: int,
    ) -> bool:
        """通过 MessagePort 发送消息

        Returns:
            发送成功返回 True，port 未注入或发送失败返回 False
        """
        if self.port is None:
            logger.warning(
                f"ResponseHandler.send: MessagePort 未注入，"
                f"消息无法发送 (user={user_id}, group={group_id})"
            )
            return False
        return await self.port.send(user_id, group_id, content, msg_id=msg_id)

    async def persist_and_send(
        self,
        user_id: str,
        group_id: str,
        content: str,
    ) -> int:
        """持久化并发送消息（便捷方法）"""
        msg_id = await self.persist(user_id, group_id, content)
        await self.send(user_id, group_id, content, msg_id)
        return msg_id
