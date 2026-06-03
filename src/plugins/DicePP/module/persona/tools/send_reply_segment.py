"""send_reply_segment 工具 — 让 LLM 按段输出回复内容

Executor 读取 / 更新 chat-local 状态，不持有全局状态。
"""

from typing import Dict, Any, Optional

from utils.logger import logger

from ..chat.segment_dispatcher import SegmentDispatcher, SegmentItem
from ..chat.segment_state import SegmentBudgetState
from ..chat.context import DEFAULT_DELAY_BEFORE
from .context import ToolContext
from .registry import ToolDef


def make_tool_def(
    target_chars: int,
    max_chars: int,
    max_delay: float,
) -> ToolDef:
    """构造 send_reply_segment 工具定义"""
    return ToolDef(
        name="send_reply_segment",
        description=(
            f"发送回复的一段内容。建议每段 {target_chars} 字，"
            f"单段文本上限 {max_chars} 字。"
            f"若有多段，可多次调用本工具。"
            f"可附带图片 URL（来自 generate_image 工具的返回结果）。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "该段回复的文本内容（与 image_url 至少提供一个）",
                },
                "image_url": {
                    "type": "string",
                    "description": "图片 URL（来自 generate_image 返回的结果）。与 content 至少提供一个。",
                },
                "delay_before": {
                    "type": "number",
                    "minimum": 0,
                    "maximum": max_delay,
                    "default": DEFAULT_DELAY_BEFORE,
                    "description": f"发送前等待秒数（0–{max_delay}）",
                },
            },
        },
    )


async def send_reply_segment_executor(args: Dict[str, Any], ctx: ToolContext) -> str:
    """执行 send_reply_segment 工具

    校验链（按顺序，失败即返回 error，不修改状态）：
    1. content.strip() != ""
    2. 0 <= delay_before <= max_delay
    3. len(content) <= max_chars
    4. segment_count + 1 <= count_max
    5. total_chars + len(content) <= hard_limit（soft_limit 发 warning）

    通过校验后：
    - buffer.append(content)
    - total_chars += len(content)
    - segment_count += 1
    - dispatcher.notify(target_key, SegmentItem(...))
    """
    content: str = args.get("content", "")
    image_url: str = args.get("image_url", "")
    delay_before: float = args.get("delay_before", DEFAULT_DELAY_BEFORE)

    state: Optional[SegmentBudgetState] = ctx.segment_state
    dispatcher: Optional[SegmentDispatcher] = ctx.segment_dispatcher

    if state is None or dispatcher is None:
        return _json_result(status="error", error="分段状态未初始化")

    # 1. at least one of content or image_url
    if content.strip() == "" and image_url.strip() == "":
        return _json_result(status="error", error="content 和 image_url 至少需要提供一个")

    # 2. delay_before bounds
    if not (0 <= delay_before <= state.limits.max_delay):
        return _json_result(
            status="error",
            error=f"delay_before 必须在 0–{state.limits.max_delay} 秒之间",
        )

    # 3. per-segment char limit (text only; image CQ code overhead ignored)
    if len(content) > state.limits.max_chars:
        remaining = max(0, state.limits.soft_limit - state.total_chars)
        return _json_result(
            status="error",
            error=f"单段不超过 {state.limits.max_chars} 字符，当前剩余预算约 {remaining} 字符，请拆分后重试",
            remaining_chars=remaining,
        )

    # 4. segment count limit
    if state.segment_count + 1 > state.limits.count_max:
        return _json_result(
            status="error",
            error=f"已超过单次回复最大段数（{state.limits.count_max}），请结束回复",
            remaining_chars=max(0, state.limits.soft_limit - state.total_chars),
        )

    # 5. total char limit (soft / hard)
    new_total = state.total_chars + len(content)
    if new_total > state.limits.hard_limit:
        return _json_result(
            status="error",
            error=f"超过单次回复上限 {state.limits.hard_limit} 字符，请精简或结束回复",
            remaining_chars=max(0, state.limits.soft_limit - state.total_chars),
        )

    # ── 通过校验，更新状态并入队 ──
    state.buffer.append(content)
    state.total_chars = new_total
    state.segment_count += 1

    target_key = SegmentDispatcher.target_key(ctx.user_id, ctx.group_id)
    dispatcher.notify(
        target_key,
        SegmentItem(
            content=content,
            delay_before=delay_before,
            user_id=ctx.user_id,
            group_id=ctx.group_id,
            image_url=image_url,
        ),
    )

    if new_total <= state.limits.soft_limit:
        return _json_result(
            status="success",
            remaining_chars=state.limits.soft_limit - new_total,
        )
    else:
        return _json_result(
            status="warning",
            warning="已超过推荐字数上限，请尽快收尾",
            remaining_chars=max(0, state.limits.soft_limit - new_total),
        )


def _json_result(status: str, remaining_chars: int = 0, **kwargs) -> str:
    import json

    result: Dict[str, Any] = {"status": status, "remaining_chars": remaining_chars}
    result.update(kwargs)
    return json.dumps(result, ensure_ascii=False)
