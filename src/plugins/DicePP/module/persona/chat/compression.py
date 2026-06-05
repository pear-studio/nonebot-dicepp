"""Session 压缩辅助函数

token 估算、切分、压缩判定。
"""
from typing import List
from utils.string import estimate_tokens


KEEP_RECENT = 10


def _get_msg_attr(msg, attr: str, default=""):
    """安全获取消息属性，兼容 dict 和对象类型。"""
    if isinstance(msg, dict):
        return msg.get(attr, default)
    return getattr(msg, attr, default)


def estimate_image_token(data_url: str) -> int:
    """估算单张 data URL 图片的 token 数。

    取数据段长度的 1/3（base64 约 4 字符 → 3 字节 → ~1.33 token/char，
    取 1/3 保守估算）。
    """
    if not data_url:
        return 0
    comma = data_url.find(",")
    if comma < 0:
        return 0
    return max(1, (len(data_url) - comma - 1) // 3)


def estimate_session_tokens(messages: List) -> int:
    """估算 session 消息列表的总 token 数。

    messages 可能是 List[PersonaSessionMessage] 或 List[dict]。
    """
    total = 0
    for msg in messages:
        content = _get_msg_attr(msg, "content")
        role = _get_msg_attr(msg, "role")
        total += estimate_tokens(role)
        if isinstance(content, str):
            total += estimate_tokens(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict):
                    if part.get("type") == "image_url":
                        image_url = part.get("image_url", {})
                        url = image_url.get("url", "") if isinstance(image_url, dict) else ""
                        if url.startswith("data:"):
                            total += estimate_image_token(url)
                    else:
                        total += estimate_tokens(part.get("text", ""))
    return int(total)


def should_compress(token_estimate: int, token_budget: int) -> bool:
    threshold = int(token_budget * 0.9)
    return token_estimate >= threshold


def ensure_tool_pairs(
    messages: List,
    keep_recent: int,
) -> tuple:
    """切分 old/recent，确保 recent 中无孤立 tool 消息。

    孤儿定义：recent 中有 tool 消息，但其 tool_call_id
    对应的 assistant 消息在 old 中（即 tool call 发起者被裁掉了）。
    """
    if len(messages) <= keep_recent:
        return [], list(messages)

    split_at = len(messages) - keep_recent
    recent = messages[split_at:]

    # 收集 recent 中 assistant 的 tool_call_ids
    available_ids = set()
    for m in recent:
        role = _get_msg_attr(m, "role")
        if role == "assistant":
            tool_calls_raw = _get_msg_attr(m, "tool_calls")
            if tool_calls_raw:
                import json
                try:
                    tcs = json.loads(tool_calls_raw) if isinstance(tool_calls_raw, str) else tool_calls_raw
                    for tc in tcs:
                        available_ids.add(tc.get("id", ""))
                except (json.JSONDecodeError, TypeError):
                    pass

    # 检测孤儿
    orphan_ids = set()
    for m in recent:
        role = _get_msg_attr(m, "role")
        if role == "tool":
            tc_id = _get_msg_attr(m, "tool_call_id")
            if tc_id and tc_id not in available_ids:
                orphan_ids.add(tc_id)

    # 向前扩展切分点
    while orphan_ids and split_at > 0:
        split_at -= 1
        m = messages[split_at]
        role = _get_msg_attr(m, "role")
        if role == "assistant":
            tool_calls_raw = _get_msg_attr(m, "tool_calls")
            if tool_calls_raw:
                import json
                try:
                    tcs = json.loads(tool_calls_raw) if isinstance(tool_calls_raw, str) else tool_calls_raw
                    for tc in tcs:
                        orphan_ids.discard(tc.get("id", ""))
                except (json.JSONDecodeError, TypeError):
                    pass

    return messages[:split_at], messages[split_at:]
