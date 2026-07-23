"""唯一消息发送出口"""
from plugins.DicePP.utils.logger import logger
from typing import Optional, Callable

from plugins.DicePP.core.bot import Bot
from plugins.DicePP.core.command.bot_cmd import BotSendMsgCommand
from plugins.DicePP.core.communication import GroupMessagePort, PrivateMessagePort
from ..data.models import MessageType

from .pipeline import MessagePipeline, SendAction
from ..tools.context import SendPort
from ..life.protocols import EventSharePort


class MessagePort(EventSharePort):
    """唯一消息发送出口 — 同时满足 SendPort (tools) 和 EventSharePort (life)

    = SendPort 契约 + pipeline（截断等元数据转换）+ 失败回调 + proxy 兜底。
    EventSharePort 已继承 SendPort，确保工具域与生活域的 send
    签名始终一致，消除 MRO 风险。
    """

    def __init__(
        self,
        bot: Bot,
        pipeline: Optional[MessagePipeline] = None,
        on_delivery_failed: Optional[Callable] = None,
    ):
        self._bot = bot
        self._pipeline = pipeline or MessagePipeline()
        self._on_delivery_failed = on_delivery_failed

    async def send(
        self,
        user_id: str,
        group_id: str,
        content: str,
        *,
        skip_history_record: Optional[bool] = None,
        msg_id: Optional[int] = None,
        message_type: MessageType = MessageType.CHAT,
    ) -> bool:
        """单条消息发送，默认记录历史。

        本方法同步等待 proxy 投递返回，调用方应避免在事件循环关键路径
        （如 1s tick / LLM 流式响应）上叠加多次裸 await；
        如需 fire-and-forget 请自行 ``asyncio.create_task`` 包装。

        分段调度器应显式传 skip_history_record=True，表示由调用方在投递成功后
        自行维护 Persona 历史；发送后事件仍会广播给 Log 等其他订阅者。

        :param skip_history_record: None 时默认 False；True 表示调用方自行维护 Persona 历史。
        :param msg_id: message_stream 表中的行 ID，供 post_send_hook 使用。
        :param message_type: 消息类型，默认 CHAT。日报路径传 SYSTEM_LOG。
        """
        if skip_history_record is None:
            skip_history_record = False
        action = SendAction(
            user_id=user_id,
            group_id=group_id,
            content=content,
            skip_history_record=skip_history_record,
        )
        processed = await self._pipeline.process([action])
        a = processed[0]
        try:
            await self._send(
                a.user_id,
                a.group_id,
                a.content,
                skip_history_record=a.skip_history_record,
                msg_id=msg_id,
                message_type=message_type,
            )
            return True
        except Exception as e:
            logger.exception("send 失败")
            if self._on_delivery_failed:
                try:
                    await self._on_delivery_failed(
                        user_id=a.user_id,
                        group_id=a.group_id,
                        content=a.content,
                        error=str(e),
                    )
                except Exception:
                    logger.exception("on_delivery_failed 回调失败")
            return False

    async def _send(
        self,
        user_id: str,
        group_id: str,
        content: str,
        skip_history_record: bool = False,
        msg_id: Optional[int] = None,
        message_type: MessageType = MessageType.CHAT,
    ) -> None:
        proxy = getattr(self._bot, "proxy", None)
        if proxy is None:
            logger.error("Bot.proxy 未配置，丢弃消息: user={} group={}", user_id, group_id)
            raise RuntimeError("Bot.proxy 未配置，消息未投递")

        if group_id:
            port = GroupMessagePort(group_id)
        else:
            port = PrivateMessagePort(user_id)
        cmd = BotSendMsgCommand(self._bot.account, content, [port])
        cmd.message_type = message_type
        if skip_history_record:
            cmd.skip_history_record = True
        if msg_id is not None:
            cmd.msg_id = msg_id
        await proxy.process_bot_command(cmd)
