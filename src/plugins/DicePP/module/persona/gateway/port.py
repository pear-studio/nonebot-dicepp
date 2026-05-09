"""唯一消息发送出口"""
from nonebot.log import logger
from typing import Optional, Callable

from core.bot import Bot
from core.command.bot_cmd import BotSendMsgCommand
from core.communication import GroupMessagePort, PrivateMessagePort

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
    ) -> bool:
        """单条消息发送，默认记录历史。

        本方法同步等待 proxy 投递返回，调用方应避免在事件循环关键路径
        （如 1s tick / LLM 流式响应）上叠加多次裸 await；
        如需 fire-and-forget 请自行 ``asyncio.create_task`` 包装。

        分段调度器应显式传 skip_history_record=True，
        把"分段消息不记历史"的决策留在分段域。

        :param skip_history_record: None 时群聊默认 True（跳过历史），私聊默认 False。
        """
        if skip_history_record is None:
            skip_history_record = bool(group_id)
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
        self, user_id: str, group_id: str, content: str, skip_history_record: bool = False
    ) -> None:
        proxy = getattr(self._bot, "proxy", None)
        if proxy is None:
            logger.error("Bot.proxy 未配置，丢弃消息: user={} group={}", user_id, group_id)
            return

        if group_id:
            port = GroupMessagePort(group_id)
        else:
            port = PrivateMessagePort(user_id)
        cmd = BotSendMsgCommand(self._bot.account, content, [port])
        if skip_history_record:
            cmd.skip_history_record = True
        await proxy.process_bot_command(cmd)
