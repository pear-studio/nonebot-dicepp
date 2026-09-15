"""Chat 正文的唯一发送工具：稳定段标识、真实投递回执。"""

from pydantic import BaseModel, ConfigDict, Field

from ..agent.runtime_types import ToolSpec, ToolResult, ToolExecutionContext
from ..chat.delivery_queue import DeliveryItem, DeliveryQueue
from ..chat.reply_session import ReplySession
from ..data.models import MessageType


def build_send_reply_segment_tool(
    delivery_queue: DeliveryQueue,
    interaction_id: str,
    user_id: str,
    group_id: str,
    max_chars: int = 2000,
    segment_count_max: int = 10,
    display_name: str = "我",
    reply_session: ReplySession | None = None,
    message_type: MessageType = MessageType.CHAT,
) -> ToolSpec:
    session = reply_session if reply_session is not None else ReplySession(delivery_queue, segment_count_max)

    class SendReplySegmentArgs(BaseModel):
        model_config = ConfigDict(extra="forbid")
        segment_id: str = Field(
            ...,
            description="本次交流内的唯一段标识，例如 s1；重试同一段必须保持原标识和正文，新段使用新标识",
        )
        content: str = Field(
            ...,
            description=f"直接发送给玩家的正文（单段上限 {max_chars} 字符），不要汇总已发送段",
        )

    async def handler(parsed, ctx: ToolExecutionContext) -> ToolResult:
        if not parsed.content.strip():
            return session.reject("content 不能为空，未发送此段。")
        if len(parsed.content) > max_chars:
            return session.reject(
                f"单段超过 {max_chars} 字符（当前 {len(parsed.content)} 字符），未发送此段；请精简后重试。"
            )
        return await session.send(parsed.segment_id, DeliveryItem(
            content=parsed.content,
            interaction_id=interaction_id,
            call_index=ctx.call_index,
            segment_phase="interim",
            user_id=user_id,
            group_id=group_id,
            message_type=message_type,
            agent_run_id=ctx.run_id,
            display_name=display_name,
        ))

    return ToolSpec(
        name="send_reply_segment",
        description=(
            "向玩家发送一段正文并等待实际投递回执。所有回复正文都必须通过此工具发送，"
            f"包括仅一条的回复；每次交流最多 {segment_count_max} 个不同段。"
            "每段用唯一 segment_id；失败重试使用原 id 和原正文，成功段不要重发。"
            "全部完成后调用 finish_reply({}) 结束，它不发送正文。"
        ),
        args_schema=SendReplySegmentArgs,
        handler=handler,
    )
