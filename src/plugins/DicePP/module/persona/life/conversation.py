"""
Conversation 模块 — 纯追加的消息线程管理

为 DM Agent 和 Character Agent（reaction 模式）提供纯追加的消息线程管理。
天内正常运行时纯追加，保证前缀不变 → LLM prompt cache 友好。
truncate() 仅在日终 compact 时调用一次，是显式的 cache-reset 点。

核心约束：
- _messages 私有，外部不可直接赋值
- 只暴露追加型接口：add_message()、add_messages()、truncate()
- system prompt 不进 _messages——由 Agent 单独持有，render() 时拼接

变更通知订阅：
- Conversation 持有 ChangeSource 列表和 opaque cursor
- fetch_notifications() 拉取通知（纯读，不突变 _messages / _cursors）
- apply_notifications() 更新 cursor 标记通知已消费（通知不进入 _messages——由 run() 模板在 LLM 消息流中临时注入）
- 调用方在 fetch → LLM call → apply 之间获得事务性保障
"""


from dataclasses import dataclass, field
from typing import Any, List, Literal, Optional, Protocol, runtime_checkable
import json

from utils.logger import logger
from utils.string import estimate_tokens


# 通知消息 content 前缀
NOTIFICATION_PREFIX = "[通知]"

# ── Snapshot & Store ──────────────────────────────────


@dataclass
class Snapshot:
    """Conversation 状态的完整序列化表示。"""

    messages: list[dict] = field(default_factory=list)
    cursors: dict[str, Any] = field(default_factory=dict)
    system_prompt: str | None = None


class Store(Protocol):
    """Conversation 持久化协议。

    不关心具体存储后端。当前仅测试用 FakeStore 实现，
    生产 SQLite 适配在 Phase 2 (ChatSession 替换) 中引入。
    """

    async def put(self, conv_id: str, snapshot: Snapshot) -> None:
        """写入（全量覆盖）。首次写入时存储层分配 conv_id。"""
        ...

    async def get(self, conv_id: str) -> Snapshot | None:
        """读取指定 conversation 的快照。"""
        ...

    async def delete(self, conv_id: str) -> None:
        """删除指定 conversation 及其数据。"""
        ...


# ── Run Config & Result ──────────────────────────────────


@dataclass
class RunConfig:
    """单轮执行配置。

    ToolLoop 根据 mode 分派执行路径（chat / collect / react）。
    """

    mode: Literal["chat", "collect", "react"] = "chat"
    tools: list[dict] | None = None
    temperature: float = 0.9
    timeout: int = 60
    max_rounds: int = 10


@dataclass
class RunResult:
    """Conversation.run() 返回值"""

    final_text: str = ""
    final_reason: str = ""  # "stop" | "max_rounds" | "error"
    delivery_performed: bool = False


class ToolLoop(Protocol):
    """LLM 执行器协议 — 与 Store 风格一致的类型安全接口。

    Conversation.run() 依赖此协议，不耦合具体实现。
    """

    async def execute(self, messages: list[dict], config: RunConfig) -> "ToolResult":
        """执行一次 LLM + 工具循环，返回增量消息和结果。"""
        ...


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

    持久化：
    - save() / open() / delete() 通过 Store 协议操作
    - 懒恢复: 首次操作时从 store 加载快照
    - compact(): 原地压缩（摘要旧消息 + 保留近期消息）

    变更通知事务性：
    - register() 注册 ChangeSource
    - fetch_notifications() 拉取通知（纯读）
    - apply_notifications() 提交通知到 _messages 并更新 cursor
    - fetch → LLM call → apply 之间自动获得事务性保障
    """

    def __init__(self, store: Optional[Store] = None,
                 tool_loop: Optional[ToolLoop] = None) -> None:
        self._store = store
        self._id: str | None = None
        self._messages: List[dict] = []
        self._change_sources: List[ChangeSource] = []
        self._cursors: dict[str, Any] = {}
        self._system_prompt: str | None = None
        self._tool_loop = tool_loop  # ToolLoop | None，注入的 LLM 执行器

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
        """更新 cursors 以标记通知已消费。

        通知仅在当轮 run() 的 LLM 消息中注入（fetch → inject → LLM call），
        不写入 _messages——下次 render() 不会重复出现。

        与 fetch_notifications() 配对使用，在 LLM 调用成功后一次性落盘 cursor。
        """
        self._cursors.update(new_cursors)

    def add_message(self, role: str, content: str) -> None:
        """追加一条消息。"""
        self._messages.append({"role": role, "content": content})

    def add_messages(self, new_messages: List[dict]) -> None:
        """追加一批消息（纯追加，无过滤）。

        过滤逻辑（system 角色、[系统指令] 前缀等）由 ToolLoop
        在返回结果前自净，不进 Conversation。
        """
        for msg in new_messages:
            self._messages.append(msg)

    # ── 执行模板 ───────────────────────────────────────

    async def run(
        self, user_input: str, config: Optional[RunConfig] = None,
        transient: str | None = None,
    ) -> RunResult:
        """单轮执行模板：fetch → build → tool_loop → apply → add_messages → save。

        tool_loop 未注入时返回 RunResult(error)。

        NOTE: render() 不依赖 self._system_prompt——运行时以参数传入的 external system_prompt
        为准。self._system_prompt 仅用于 Snapshot 持久化/恢复（Conversation.open() 路径）。
        与 Agent._process() 的执行模式重复将在 Phase 3（Agent 基类统一）时消除，
        届时 Agent._process() 委托给 Conversation.run()。
        transient 参数是过渡方案，多源注入需求出现时迁移至 RunConfig.transient_messages。
        """
        if config is None:
            config = RunConfig()

        # 1. fetch — 纯读，不改变状态
        notifs, cursors = await self.fetch_notifications()

        # 2. build — 拼装完整消息
        messages = self.render(self._system_prompt or "")
        for n in notifs:
            messages.append(n.to_message())
        messages.append({"role": "user", "content": user_input})
        if transient:
            messages.append({"role": "user", "name": "系统", "content": transient})

        # 3. execute — 委托给 ToolLoop
        if self._tool_loop is None:
            return RunResult(final_text="", final_reason="error: no tool_loop")

        tool_result = await self._tool_loop.execute(messages, config)

        # 4. apply — 提交 cursor
        self.apply_notifications(notifs, cursors)

        # 5. extend — 追加本轮增量（new_messages 只含 LLM 产出的新消息）
        self.add_message("user", user_input)
        self.add_messages(tool_result.new_messages)

        # 6. save — 自动持久化
        await self.save()

        return RunResult(
            final_text=tool_result.final_text,  # type: ignore[attr-defined]
            final_reason=tool_result.final_reason,  # type: ignore[attr-defined]
            delivery_performed=tool_result.delivery_performed,  # type: ignore[attr-defined]
        )

    def get_messages(self) -> List[dict]:
        """返回内部消息列表的副本（不含 system prompt）。

        调用方可在此基础上拼接 system prompt、待提交通知、用户消息等。
        """
        return list(self._messages)

    # ── 持久化 ───────────────────────────────────────

    @property
    def id(self) -> str | None:
        """当前 conversation 的唯一标识（由 Store 分配）。"""
        return self._id

    @property
    def system_prompt(self) -> str | None:
        """外部管理的 system prompt，render() 时拼接在消息列表头部。"""
        return self._system_prompt

    @system_prompt.setter
    def system_prompt(self, value: str) -> None:
        self._system_prompt = value

    async def save(self) -> None:
        """持久化当前快照。

        store 为 None 时跳过（纯内存模式）。首次 save 由 Store 分配 conv_id。
        cursor 序列化由 json.dumps 校验——不可序列化的 cursor 会在此时抛出 TypeError。
        """
        if self._store is None:
            return
        snapshot = Snapshot(
            # 当前所有 messages 的 content 均为 str；若未来支持多模态 list content，改用 copy.deepcopy
            messages=[dict(m) for m in self._messages],
            cursors=self._cursors,
            system_prompt=self._system_prompt,
        )
        await self._store.put(self._id or "", snapshot)
        # Store 实现层可能分配新 id；这里不做假设

    @classmethod
    async def open(cls, conv_id: str, store: Store) -> "Conversation":
        """从存储恢复 Conversation。不存在时创建新的空实例。

        创建后调用方可注册 ChangeSource、设置 system_prompt，然后首次 run()
        将触发懒恢复从 store 加载消息和 cursor。
        """
        conv = cls(store=store)
        conv._id = conv_id
        snapshot = await store.get(conv_id)
        if snapshot is not None:
            conv._messages = snapshot.messages
            conv._cursors = snapshot.cursors
            if snapshot.system_prompt is not None:
                conv._system_prompt = snapshot.system_prompt
        return conv

    async def delete(self) -> None:
        """删除持久化的 conversation。store 为 None 时只清内存。"""
        if self._store is not None and self._id is not None:
            await self._store.delete(self._id)
        self.clear()

    # ── Compact ───────────────────────────────────────

    async def compact(self, keep_recent: int, router=None) -> str:
        """原地压缩：LLM 摘要旧消息 + 保留近期消息 → 替换 _messages → save。

        保留 cursors 不变——compact 是内存管理操作，不重新触发已有通知。

        Returns:
            生成的摘要文本（用于日志/调试）。
        """
        if len(self._messages) <= keep_recent:
            return ""

        old_msgs = self._messages[:-keep_recent]
        recent = self._messages[-keep_recent:]

        summary_text = await _llm_compact_summarize(router, old_msgs)

        summary_msg = {
            "role": "user",
            "content": f"{NOTIFICATION_PREFIX} 之前的对话摘要：{summary_text}",
        }
        self._messages = [summary_msg] + list(recent)
        await self.save()
        return summary_text

    # ── Token 估算 ───────────────────────────────────────

    def estimate_tokens(self) -> int:
        """估算 _messages 的总 token 数（不含 system_prompt）。

        调用方自行加上 system_prompt 的 token 得到完整上下文大小。
        """
        total = 0.0
        for msg in self._messages:
            content = msg.get("content", "")
            if isinstance(content, str):
                total += estimate_tokens(content)
            elif isinstance(content, list):
                for p in content:
                    if isinstance(p, dict):
                        total += estimate_tokens(p.get("text", ""))
        return int(total)

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


# ── LLM 摘要辅助函数 ──────────────────────────────────


async def _llm_compact_summarize(router, old_msgs: list) -> str:
    """调用 LLM 对旧消息生成摘要。失败时返回硬截断兜底文本。"""
    if router is None:
        return _fallback_summary()

    lines = []
    for msg in old_msgs:
        role = msg.get("role", "")
        content = msg.get("content", "")
        if role == "user":
            lines.append(f"用户：{content}")
        elif role == "assistant":
            lines.append(f"角色：{content}")

    if not lines:
        return _fallback_summary()

    conversation_text = "\n".join(lines)
    messages = [
        {"role": "system", "content": "你是一个对话摘要助手。请用一段简短的中文总结以下角色扮演对话的关键信息。要求：只记录明确发生的内容，不要推断或编造；保留准确的名称、数字、时间等具体信息；用 200-300 字概括。"},
        {"role": "user", "content": f"对话记录：\n{conversation_text}"},
    ]

    try:
        from ..llm.selection import SUMMARIZE
        candidates = router.build_candidates(SUMMARIZE)
        for key in candidates:
            provider = router.get_model_provider(key)
            if provider is None:
                continue
            try:
                resp = await provider.generate(
                    messages=messages, temperature=0.3, timeout=30,
                )
                return resp.content.strip() if resp and resp.content else _fallback_summary()
            except Exception:
                logger.warning(
                    "Compact LLM summarizer provider 调用失败", exc_info=True,
                )
                continue
    except Exception:
        logger.warning(
            "Conversation compact LLM 摘要路由失败", exc_info=True,
        )
    return _fallback_summary()


_FALLBACK_SUMMARY_TEXT = "之前的对话内容超出上下文限制，部分历史已丢弃。"


def _fallback_summary() -> str:
    return _FALLBACK_SUMMARY_TEXT
