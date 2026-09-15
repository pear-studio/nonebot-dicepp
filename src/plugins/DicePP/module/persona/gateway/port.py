"""唯一消息发送出口"""
from plugins.DicePP.utils.logger import logger
from typing import Optional, Callable

from plugins.DicePP.core.bot import Bot
from plugins.DicePP.core.command.bot_cmd import BotSendMsgCommand
from plugins.DicePP.core.communication import GroupMessagePort, PrivateMessagePort
from ..data.models import MessageType

from .pipeline import MessagePipeline, SendAction
from ..tools.context import SendPort


class _DeliveryNotAttempted(RuntimeError):
    """发送前已确认缺少必要依赖，消息没有进入 proxy。"""


class MessagePort(SendPort):
    """唯一消息发送出口 — 满足 SendPort (tools)

    = SendPort 契约 + pipeline（截断等元数据转换）+ 失败回调 + proxy 兜底。
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
    ) -> bool | None:
        """单条消息发送，默认记录历史。

        本方法同步等待 proxy 投递返回，调用方应避免在事件循环关键路径
        （如 1s tick / LLM 流式响应）上叠加多次裸 await；
        如需 fire-and-forget 请自行 ``asyncio.create_task`` 包装。

        分段调度器应显式传 skip_history_record=True，表示由调用方在投递成功后
        自行维护 Persona 历史；发送后事件仍会广播给 Log 等其他订阅者。

        返回 True 表示 proxy 已确认成功；False 表示确认没有尝试投递；
        None 表示 proxy 异常或超时，无法确定消息是否已经送达，不能据此重发。

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
        a = action
        failure_outcome: bool | None = False
        try:
            processed = await self._pipeline.process([action])
            a = processed[0]
            # Pipeline 只转换元数据；跨入投递调用后按未知处理，除非明确未尝试。
            failure_outcome = None
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
            if isinstance(e, _DeliveryNotAttempted):
                failure_outcome = False
            logger.exception("send 失败")
            if self._on_delivery_failed:
                try:
                    await self._on_delivery_failed(
                        user_id=a.user_id,
                        group_id=a.group_id,
                        content=a.content,
                        error=str(e),
                        delivery_unknown=failure_outcome is None,
                    )
                except Exception:
                    logger.exception("on_delivery_failed 回调失败")
            return failure_outcome

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
            raise _DeliveryNotAttempted("Bot.proxy 未配置，消息未投递")

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
