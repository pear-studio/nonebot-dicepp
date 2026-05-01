"""分段延迟发送工具（预留骨架）"""
from .context import ToolContext


SEND_MESSAGE_TOOL = {
    "name": "send_message",
    "description": "分段延迟发送消息给用户",
    "parameters": {
        "type": "object",
        "properties": {
            "segments": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "content": {"type": "string", "description": "消息内容"},
                        "delay_seconds": {
                            "type": "number",
                            "default": 0,
                            "description": "延迟秒数，支持浮点（如 1.5 秒）",
                        },
                    },
                    "required": ["content"],
                },
            },
        },
        "required": ["segments"],
    },
}


async def send_message_executor(args: dict, ctx: ToolContext) -> str:
    """执行分段发送"""
    if ctx.send is None:
        return "发送功能不可用"

    segments = args.get("segments", [])
    if not segments:
        return "无内容可发送"

    await ctx.send.send_segmented(ctx.user_id, ctx.group_id, segments)
    return "消息已加入发送队列"
