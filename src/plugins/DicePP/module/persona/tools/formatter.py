"""消息搜索结果格式化"""

from ..transcript import format_assistant_message, format_event_message, format_player_message


def format_message_results(results, max_chars: int = 180) -> str:
    """格式化消息检索结果，每行直接携带稳定账号与可读昵称。"""
    lines = []
    for msg in results:
        content = msg.content
        if len(content) > max_chars:
            content = content[:max_chars] + "..."

        message_type = getattr(msg, "type", "")
        type_value = getattr(message_type, "value", message_type)
        is_event = type_value in {"event", "system_notice", "system_log"}
        if is_event:
            line = format_event_message(content, msg.created_at)
        elif msg.role == "assistant":
            line = format_assistant_message(
                content, msg.created_at, flattened=True,
            )
        elif msg.role == "user":
            line = format_player_message(
                content,
                msg.user_id,
                msg.display_name or msg.user_id,
                msg.created_at,
            )
        else:
            line = format_event_message(content, msg.created_at)
        lines.append(line)

    return "\n".join(lines)
