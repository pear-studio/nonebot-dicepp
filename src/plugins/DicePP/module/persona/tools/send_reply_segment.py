"""send_reply_segment 工具 — 让 LLM 按段输出回复内容

T5: send_reply_segment 转为普通 ToolSpec（只保留 content 参数）。
handler 把中间消息交给 DeliveryQueue，写入 message stream，segment_phase="interim"。
"""

from typing import Dict, Any, Optional

from plugins.DicePP.utils.logger import logger

from ..agent.runtime_types import ToolSpec, ToolResult, ToolExecutionContext
from pydantic import BaseModel, Field


# ── ToolSpec 定义 ───────────────────────────────────────────

def build_send_reply_segment_tool(
    delivery_queue: "DeliveryQueue",
    interaction_id: str,
    user_id: str,
    group_id: str,
    max_chars: int = 2000,
    segment_count_max: int = 10,
    display_name: str = "我",
) -> "ToolSpec":
    """T5: 构建 send_reply_segment 普通工具。

    LLM 可见 schema 只包含 content。
    handler 把中间消息交给 DeliveryQueue，segment_phase="interim"。

    R6: 恢复长度约束——LLM 可见 max_chars 描述，handler 超限返回 error。
    segment_count_max: 单次交互最大分段数，超出返回 error。
    """
    from pydantic import BaseModel, Field
    from ..agent.runtime_types import ToolSpec, ToolResult, ToolExecutionContext

    class SendReplySegmentArgs(BaseModel):
        content: str = Field(
            ...,
            description=f"该段回复的文本内容（单段上限 {max_chars} 字符）",
        )

    async def handler(parsed, ctx: "ToolExecutionContext") -> ToolResult:
        from ..chat.delivery_queue import DeliveryItem

        if not parsed.content.strip():
            return ToolResult(observation="content 不能为空", status="error")

        # R6: 单段长度检查
        if len(parsed.content) > max_chars:
            return ToolResult(
                observation=(
                    f"单段超过 {max_chars} 字符（当前 {len(parsed.content)} 字符），"
                    f"请精简后重试"
                ),
                status="error",
            )

        # 段数硬限：真实 DeliveryQueue 支持 enqueue 前同步 reserve；
        # 测试替身缺少该方法时回退到只读计数。
        reserve = getattr(delivery_queue, "try_reserve_interim", None)
        if reserve is not None:
            accepted = reserve(interaction_id, segment_count_max)
            current_count = delivery_queue.count_interim(interaction_id)
            if not accepted:
                return ToolResult(
                    observation=(
                        f"已发送 {current_count} 段，达到上限 {segment_count_max} 段。"
                        f"请调用 send_reply 提交最终内容。"
                    ),
                    status="error",
                )
        else:
            count = getattr(delivery_queue, "count_interim", None)
            current_count = count(interaction_id) if count is not None else 0
            if current_count >= segment_count_max:
                return ToolResult(
                    observation=(
                        f"已发送 {current_count} 段，达到上限 {segment_count_max} 段。"
                        f"请调用 send_reply 提交最终内容。"
                    ),
                    status="error",
                )

        delivery_queue.enqueue(DeliveryItem(
            content=parsed.content,
            interaction_id=interaction_id,
            call_index=ctx.call_index,
            segment_phase="interim",
            user_id=user_id,
            group_id=group_id,
            agent_run_id=ctx.run_id,
            display_name=display_name,
        ))
        return ToolResult(observation=f"第 {ctx.call_index + 1} 段已发送")

    return ToolSpec(
        name="send_reply_segment",
        description=(
            "发送回复的前置分段内容。最多调用 {segment_count_max} 次，"
            "最后必须调用 send_reply 提交。"
        ).format(segment_count_max=segment_count_max),
        args_schema=SendReplySegmentArgs,
        handler=handler,
    )
