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
- Conversation 持有 ChangeSource 列表，通过 pull_notifications() 拉取变更通知
- ChangeSource 是外部注入的有状态对象，Conversation 代替存储不透明 cursor
- 通知消息混入 _messages，格式为 {"role": "user", "name": ..., "content": "[通知] ..."}
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


@runtime_checkable
class ChangeSource(Protocol):
    """变更来源协议

    Conversation 在 pull_notifications() 中调用 update(cursor)，
    传入上次产生的 cursor（首次为 None），拿到通知列表和新 cursor。
    """

    source_id: str
    priority: int
    name: str

    async def update(self, cursor: Any) -> "tuple[list[Notification], Any]":
        """拉取变更通知。

        Args:
            cursor: 上次 update 返回的 cursor，首次为 None

        Returns:
            (notifications, new_cursor): 本次变更通知列表 + 新 cursor
        """
        ...


class Conversation:
    """纯追加的消息线程。

    system prompt 不进 _messages——由 Agent 单独持有，render() 时拼接。

    变更通知订阅：
    - 通过 register() 注册 ChangeSource
    - pull_notifications() 拉取变更通知，通知作为 user 消息混入 _messages
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

    async def pull_notifications(self) -> None:
        """遍历所有 ChangeSource，拉取变更通知并注入 _messages。

        每个 source.update() 用 try/except 包裹——单个 source 失败
        记录警告日志并继续处理其余 source，不阻断整体流程。
        """
        for source in self._change_sources:
            try:
                cursor = self._cursors.get(source.source_id)
                notifications, new_cursor = await source.update(cursor)
                self._cursors[source.source_id] = new_cursor
                # TODO (B-260630-26d6a7): 通知消息 role='user' / name + '[通知]' 前缀为
                # 暂定方案。待 real LLM 验证 system role 行为差异后统一迁移——若结论为
                # 改用 system role，此处消息格式、NOTIFICATION_PREFIX 常量及 render() 逻辑
                # 需联动修改。
                for n in notifications:
                    self._messages.append({
                        "role": "user",
                        "name": n.name,
                        "content": f"{NOTIFICATION_PREFIX} {n.content}",
                    })
            except Exception:
                logger.warning(
                    f"Conversation.pull_notifications: "
                    f"source {source.source_id} update 失败，已跳过",
                    exc_info=True,
                )

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
