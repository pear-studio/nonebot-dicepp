"""
Conversation 模块 — 纯追加的消息线程管理

为 DM Agent 和 Character Agent（reaction 模式）提供纯追加的消息线程管理。
天内正常运行时纯追加，保证前缀不变 → LLM prompt cache 友好。
truncate() 仅在日终 compact 时调用一次，是显式的 cache-reset 点。

核心约束：
- _messages 私有，外部不可直接赋值
- 只暴露追加型接口：add_user()、extend()、truncate()
- system prompt 不进 _messages——由 Agent 单独持有，render() 时拼接
"""

from typing import List


# 内部纠正注入消息特征前缀
# 与 AgentLoop._inject_correction 中的前缀保持同步；修改纠正机制时需同步更新此处
_CORRECTION_PREFIXES = ("[系统指令]",)  # 与 AgentLoop._inject_correction 前缀保持同步


class Conversation:
    """纯追加的消息线程。

    system prompt 不进 _messages——由 Agent 单独持有，render() 时拼接。
    """

    def __init__(self) -> None:
        self._messages: List[dict] = []

    def add_user(self, content: str) -> None:
        """追加一条 user 消息。"""
        self._messages.append({"role": "user", "content": content})

    def extend(self, new_messages: List[dict]) -> None:
        """追加执行层返回的增量消息。

        只追加 role in {assistant, tool, user} 的消息，
        过滤掉内部纠正注入等非对话消息。
        """
        for msg in new_messages:
            role = msg.get("role", "")
            if role not in ("assistant", "tool", "user"):
                continue
            # 过滤内部纠正注入（role=user 且 content 以 [系统指令] 开头）
            if role == "user":
                content = msg.get("content", "")
                if isinstance(content, str) and content.startswith(_CORRECTION_PREFIXES):
                    continue
            self._messages.append(msg)

    def render(self, system_prompt: str) -> List[dict]:
        """返回完整 messages 列表，system prompt 在最前面。

        Args:
            system_prompt: Agent 持有的系统提示词

        Returns:
            完整的消息列表，system prompt + _messages
        """
        return [{"role": "system", "content": system_prompt}, *self._messages]

    def truncate(self, keep_recent: int) -> None:
        """截断旧消息，保留最近 N 条。

        当前实现为朴素尾部截取——从尾部向前保留最近 keep_recent 条消息，
        不验证 tool_call_id 或消息角色配对关系。

        TODO: 实现配对感知截断（确保 assistant(tool_call) ↔ tool_result 不被打断）
        或替换为 LLM compact（summarize 旧消息为一条摘要消息）。
        """
        if keep_recent <= 0:
            self._messages.clear()
            return
        if keep_recent >= len(self._messages):
            return

        # 朴素尾部截取：从尾部向前取 keep_recent 条
        # TODO: 实现配对感知截断
        result: List[dict] = []
        count = 0
        for msg in reversed(self._messages):
            result.append(msg)
            count += 1
            if count >= keep_recent:
                break

        result.reverse()
        self._messages = result

    def clear(self) -> None:
        """清空所有消息（用于跨天重置）。"""
        self._messages.clear()

    @property
    def length(self) -> int:
        """当前消息数（不含 system prompt）。"""
        return len(self._messages)
