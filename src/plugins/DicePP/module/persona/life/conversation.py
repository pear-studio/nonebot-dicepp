"""
Conversation 模块 — 纯追加的消息线程管理

为 DM Agent 和 Character Agent（reaction 模式）提供纯追加的消息线程管理。
天内正常运行时纯追加，保证前缀不变 → LLM prompt cache 友好。
truncate() 仅在日终 compact 时调用一次，是显式的 cache-reset 点。

核心约束：
- _messages 私有，外部不可直接赋值
- 只暴露追加型接口：add_user()、extend()、truncate()
- system prompt 不进 _messages——由 Agent 单独持有，render() 时拼接

变更通知订阅：
- Conversation 持有 ChangeSource 列表和 opaque cursor
- fetch_notifications() 拉取通知（纯读，不突变 _messages / _cursors）
- apply_notifications() 将通知写入 _messages 并更新 cursor
- 调用方在 fetch → LLM call → apply 之间获得事务性保障
"""


from dataclasses import dataclass
from typing import Any, List, Optional, Protocol, runtime_checkable

from utils.logger import logger


# 内部纠正注入消息特征前缀
# 与 AgentLoop._inject_correction 中的前缀保持同步；修改纠正机制时需同步更新此处
_CORRECTION_PREFIXES = ("[系统指令]",)  # 与 AgentLoop._inject_correction 前缀保持同步

# 通知消息 content 前缀
NOTIFICATION_PREFIX = "[通知]"


# ── Notification & ChangeSource ──────────────────────────────────


@dataclass
class Notification:
    """变更通知 — ChangeSource.update() 的产出"""

    source_id: str
    content: str
    name: str = "系统通知"

    def to_message(self) -> dict:
        """转为 OpenAI 消息格式。"""
        return {
            "role": "user",
            "name": self.name,
            "content": f"{NOTIFICATION_PREFIX} {self.content}",
        }


@runtime_checkable
class ChangeSource(Protocol):
    """变更来源协议

    Conversation 在 fetch_notifications() 中调用 update(cursor)，
    传入上次已提交的 cursor（首次为 None），拿到通知列表和新 cursor。

    update() 必须幂等：以相同 cursor 重复调用应返回相同结果。
    cursor 由 Conversation 外部管理，update() 不得自行持久化状态。
    """

    source_id: str
    priority: int
    name: str

    async def update(self, cursor: Any) -> "tuple[list[Notification], Any]":
        """拉取变更通知（幂等）。

        Args:
            cursor: 上次 apply_notifications() 提交的 cursor，首次为 None

        Returns:
            (notifications, new_cursor): 本次变更通知列表 + 新 cursor
        """
        ...


class Conversation:
    """纯追加的消息线程。

    system prompt 不进 _messages——由 Agent 单独持有，render() 时拼接。

    变更通知事务性：
    - register() 注册 ChangeSource
    - fetch_notifications() 拉取通知（纯读）
    - apply_notifications() 提交通知到 _messages 并更新 cursor
    - fetch → LLM call → apply 之间自动获得事务性保障
    """

    def __init__(self) -> None:
        self._messages: List[dict] = []
        self._change_sources: List[ChangeSource] = []
        self._cursors: dict[str, Any] = {}

    # ── ChangeSource 管理 ───────────────────────────────────────

    def register(self, source: ChangeSource) -> None:
        """注册变更来源。

        按 source_id 幂等：同 id 重复注册会替换之前的。
        注册后按 (priority, source_id) 排序，保证通知注入顺序稳定。
        """
        # 幂等：移除同 source_id 的旧条目
        self._change_sources = [
            s for s in self._change_sources if s.source_id != source.source_id
        ]
        self._cursors.pop(source.source_id, None)
        self._change_sources.append(source)
        self._change_sources.sort(key=lambda s: (s.priority, s.source_id))

    async def fetch_notifications(self) -> tuple[list[Notification], dict[str, Any]]:
        """拉取所有 ChangeSource 的待推送通知。

        纯读操作——不改变 _messages 和 _cursors。调用方拿到通知列表和待提交的
        cursor 快照后，可在 LLM 调用成功后通过 apply_notifications() 一次性落盘。

        每个 source.update() 用 try/except 包裹——单个 source 失败
        记录警告日志并继续处理其余 source，不阻断整体流程。

        Returns:
            (notifications, new_cursors): 通知列表 + 待提交的 cursor 映射
        """
        all_notifs: list[Notification] = []
        new_cursors: dict[str, Any] = {}
        for source in self._change_sources:
            try:
                cursor = self._cursors.get(source.source_id)
                notifications, new_cursor = await source.update(cursor)
                all_notifs.extend(notifications)
                new_cursors[source.source_id] = new_cursor
            except Exception:
                logger.warning(
                    f"Conversation.fetch_notifications: "
                    f"source {source.source_id} update 失败，已跳过",
                    exc_info=True,
                )
        return all_notifs, new_cursors

    def apply_notifications(
        self, notifications: list[Notification], new_cursors: dict[str, Any]
    ) -> None:
        """将通知写入 _messages 并更新 cursors。

        与 fetch_notifications() 配对使用，在 LLM 调用成功后一次性落盘。
        """
        for n in notifications:
            self._messages.append(n.to_message())
        self._cursors.update(new_cursors)

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

    def get_messages(self) -> List[dict]:
        """返回内部消息列表的副本（不含 system prompt）。

        调用方可在此基础上拼接 system prompt、待提交通知、用户消息等。
        """
        return list(self._messages)

    def render(self, system_prompt: str) -> List[dict]:
        """返回完整 messages 列表，system prompt 在最前面。

        Args:
            system_prompt: Agent 持有的系统提示词

        Returns:
            完整的消息列表，system prompt + _messages
        """
        return [{"role": "system", "content": system_prompt}, *self.get_messages()]

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
        self._cursors.clear()

    def get_keys_by_message_prefix(self, prefix: str) -> set[str]:
        """扫描 _messages，返回所有以 prefix 开头消息中提取的 key 集合。

        用于 DMAgent story_deck 注入去重：扫描 Conversation 中
        已注入的 [故事提示 (story_deck)] 消息，提取已注入的条目 key。

        格式约定：prefix 开头的消息中，每行 "- key (type)：..." 的 key
        部分被提取。此方法是 Conversation 对消息格式的单一封装点，
        调用方不直接访问 _messages。
        """
        keys: set[str] = set()
        for msg in self._messages:
            content = msg.get("content", "")
            if isinstance(content, str) and content.startswith(prefix):
                for line in content.split("\n"):
                    line = line.strip()
                    if line.startswith("- "):
                        from ..tools.story_deck import parse_injection_key
                        key_part = parse_injection_key(line)
                        if key_part:
                            keys.add(key_part)
        return keys

    @property
    def length(self) -> int:
        """当前消息数（不含 system prompt）。"""
        return len(self._messages)
