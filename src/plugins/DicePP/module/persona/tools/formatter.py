"""消息搜索结果格式化"""
from typing import Dict, List


def format_message_results(results, max_chars: int = 180) -> str:
    """格式化消息检索结果为纯文本，参与者映射提供匿名化"""
    participants: Dict[str, str] = {}
    uids = sorted({msg.user_id for msg in results if msg.role != "assistant" and msg.user_id})
    anon_map: Dict[str, str] = {uid: f"用户{i + 1}" for i, uid in enumerate(uids)}
    for msg in results:
        if msg.role == "assistant":
            participants["assistant"] = "我"
        elif msg.user_id:
            participants[anon_map[msg.user_id]] = msg.display_name or msg.user_id

    lines = ["参与者:"]
    for uid, name in participants.items():
        lines.append(f"{uid} -> {name}")
    lines.append("")

    for msg in results:
        time_str = msg.created_at.strftime("%Y-%m-%d %H:%M:%S") if msg.created_at else ""
        if msg.role == "assistant":
            speaker = "我"
        else:
            speaker = msg.display_name or msg.user_id

        content = msg.content
        if len(content) > max_chars:
            content = content[:max_chars] + "..."

        lines.append(f"[{time_str}] [{speaker}] {content}")

    return "\n".join(lines)
