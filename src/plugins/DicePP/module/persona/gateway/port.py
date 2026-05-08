"""唯一消息发送出口"""
import asyncio
from nonebot.log import logger
from typing import Optional, Callable, List, Dict

from core.bot import Bot
from core.command.bot_cmd import BotSendMsgCommand
from core.communication import GroupMessagePort, PrivateMessagePort

from .pipeline import MessagePipeline, SendAction
from ..tools.context import SendPort
from ..life.protocols import EventSharePort


class MessagePort(EventSharePort):
    """唯一消息发送出口 — 同时满足 SendPort (tools) 和 EventSharePort (life)

    EventSharePort 已继承 SendPort，确保工具域与生活域的 send_segmented
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
        self._bg_tasks: set[asyncio.Task] = set()

    async def send_segmented(
        self, user_id: str, group_id: str, segments: List[Dict]
    ) -> bool:
        """批量发送分段消息，默认记录历史。

        与 send_now 的差异：send_segmented 走 pipeline + 后台任务，
        send_now 为即时单条发送。skip_history_record 默认均为 False，
        分段域如需跳过应在调用侧显式指定。
        """
        actions = [
            SendAction(
                user_id=user_id,
                group_id=group_id,
                content=s["content"],
                delay_seconds=s.get("delay", 0),
                skip_history_record=s.get("skip_history_record", False),
            )
            for s in segments
        ]
        processed = await self._pipeline.process(actions)
        self._spawn_background(processed)
        return True  # fire-and-forget

    async def send_now(
        self,
        user_id: str,
        group_id: str,
        content: str,
        *,
        skip_history_record: bool = False,
    ) -> bool:
        """即时单条发送，默认记录历史。

        分段调度器应显式传 skip_history_record=True，
        把"分段消息不记历史"的决策留在分段域。
        """
        action = SendAction(
            user_id=user_id,
            group_id=group_id,
            content=content,
            delay_seconds=0,
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
            logger.exception("send_now 失败")
            if self._on_delivery_failed:
                await self._on_delivery_failed(
                    user_id=a.user_id,
                    group_id=a.group_id,
                    content=a.content,
                    error=str(e),
                )
            return False

    def _spawn_background(self, actions: List[SendAction]) -> None:
        task = asyncio.create_task(self._run_actions(actions))
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)

    async def _run_actions(self, actions: List[SendAction]) -> None:
        for action in actions:
            if action.delay_seconds > 0:
                await asyncio.sleep(action.delay_seconds)
            try:
                await self._send(
                    action.user_id,
                    action.group_id,
                    action.content,
                    skip_history_record=action.skip_history_record,
                )
            except Exception as e:
                logger.exception("后台发送失败")
                if self._on_delivery_failed:
                    await self._on_delivery_failed(
                        user_id=action.user_id,
                        group_id=action.group_id,
                        content=action.content,
                        error=str(e),
                    )

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
