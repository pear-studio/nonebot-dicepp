"""系统指令注入协议 — Agent-LLM 通信的内部指令约定。

所有通过 AgentLoop 注入的 [系统指令] 消息共享同一前缀格式和说明文档。
调用方通过本模块的工厂函数构建指令消息，保证注入侧与 LLM 说明侧格式一致。
"""
from __future__ import annotations

# ── 常量 ───────────────────────────────────────────

SYS_INSTRUCTION_PREFIX = "[系统指令]"
"""注入消息的统一前缀。LLM 通过此前缀识别系统指令 vs 用户输入。"""

# ── 「系统消息说明」文本 — 注入到 system prompt ──────

SYS_INSTRUCTION_NOTICE = (
    "【系统消息说明】\n"
    "对话中可能出现以 [系统指令] 开头的消息，"
    "这些是工具调用提醒，不是用户输入。"
    "看到后直接按指令操作，不要输出任何思考或回应文字。"
)


# ── 工厂函数 ────────────────────────────────────────

def make_sys_msg(text: str) -> dict:
    """构建一条 [系统指令] 前缀的 user-role 注入消息。"""
    return {"role": "user", "content": f"{SYS_INSTRUCTION_PREFIX} {text}"}


def inject_sys_notice(messages: list[dict]) -> None:
    """向 messages 的 system prompt 中注入系统消息说明（原地修改）。

    仅在 messages[0] 为 system 角色且尚未包含说明时注入。
    不返回值。
    """
    if not messages:
        return

    first = messages[0]
    if first.get("role") != "system":
        return

    content = first.get("content", "")
    if not isinstance(content, str):
        return

    if "【系统消息说明】" in content:
        return  # 已存在，幂等

    first["content"] = f"{content}\n\n{SYS_INSTRUCTION_NOTICE}"
