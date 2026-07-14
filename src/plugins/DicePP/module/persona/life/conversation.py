"""
Conversation 模块 — 纯追加的消息线程管理

为 DM Agent 和 Character Agent（reaction 模式）提供纯追加的消息线程管理。
天内正常运行时纯追加，保证前缀不变 → LLM prompt cache 友好。
truncate() 仅在日终 compact 时调用一次，是显式的 cache-reset 点。

核心约束：
- _messages 私有，外部不可直接赋值
- 只暴露追加型接口：add_message()、add_messages()、truncate()
- system prompt 不进 _messages——每次 run() 时由调用方显式传入

变更通知事务性：
- Conversation 持有 ChangeSource 列表和 opaque cursor
- fetch_notifications() 拉取通知（纯读，不突变 _messages / _cursors）
- run() 成功后将通知 context 持久化到 _messages 并 apply cursor
- run() 失败时不提交通知 context，不推进 cursor
- 调用方在 fetch → LLM call → apply 之间获得事务性保障

T3 重构：
- system_prompt 不再持久化到 Snapshot，每次 run() 由调用方显式传入
- Conversation.run() 走新 AgentRuntime.run(AgentRunRequest) 路径
- message_delta 不含 user_input，Conversation 自己负责追加
- transient_context_messages 仅本轮可见，不持久化
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional, Protocol, runtime_checkable
import re
import json
import uuid

from utils.logger import logger
from utils.string import estimate_tokens

from ..agent.runtime_types import (
    AgentRunRequest,
    AgentRunResult,
    LoopLimits,
    OutputSpec,
    RunCompletion,
    RunMetadata,
    ToolKit,
)
from ..llm.selection import CHAT, SelectionPolicy


# 通知消息 content 前缀
NOTIFICATION_PREFIX = "[通知]"

# Stage B token 估算：每条消息的固定结构开销（role 标签/消息分隔），
# estimate_tokens 只算正文，补此余量使总估算不低于真实用量。
_MESSAGE_TOKEN_OVERHEAD = 4

# 引用条目相关常量
ENTRY_TYPE_REF = "ref"
ENTRY_TYPE_OWN = "own"
# 悬空引用（被引用的 message_stream 已被清除）的兜底正文
DANGLING_REF_FALLBACK = "[对话历史已被清除]"

# stream_loader：按 message_stream_id 批量取回权威消息记录。
# 返回 {id: obj}，obj 需提供 .role 与 .content（如 UnifiedMessage）。
StreamLoader = Callable[[List[int]], Awaitable[Dict[int, Any]]]


# 说话者名（OpenAI 原生 name 字段）净化。display_name 源自用户可控的 QQ 群名片/
# 昵称，注入前须净化：控制字符可破坏 HTTP/JSON 框架或触发严格端点校验，空白为历史
# OpenAI name 禁用项（QQ 昵称常含空格），超长可被端点拒绝。保留 CJK/emoji 等可见字符
# ——现网 CJK name 已稳定工作，证明端点容忍非 ASCII，无需剔除（剔除反而抹掉说话者身份）。
_NAME_MAX_LEN = 64
_WHITESPACE_RUN_RE = re.compile(r"\s+")
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def _sanitize_speaker_name(raw: str) -> str:
    """净化说话者名以安全注入 OpenAI name 字段。

    步骤：空白（含 \\n\\t）折叠为单下划线 → 剔除其余控制字符 → 去首尾下划线 →
    截断到安全长度。净化后为空（如全控制字符）返回空串，调用方据此省略 name。
    """
    if not raw:
        return ""
    cleaned = _WHITESPACE_RUN_RE.sub("_", raw)
    cleaned = _CONTROL_CHARS_RE.sub("", cleaned)
    cleaned = cleaned.strip("_")
    if len(cleaned) > _NAME_MAX_LEN:
        cleaned = cleaned[:_NAME_MAX_LEN]
    return cleaned


# ── Snapshot & Store ──────────────────────────────────


@dataclass
class Snapshot:
    """Conversation 状态的完整序列化表示。

    T3: system_prompt 不再持久化，每次 run() 由调用方显式传入。
    """

    messages: list[dict] = field(default_factory=list)
    cursors: dict[str, Any] = field(default_factory=dict)


class Store(Protocol):
    """Conversation 持久化协议。

    不关心具体存储后端。当前仅测试用 FakeStore 实现，
    生产 SQLite 适配在 Phase 2 (ChatSession 替换) 中引入。
    """

    async def put(self, conv_id: str, snapshot: Snapshot) -> str:
        """写入（全量覆盖）。首次写入时存储层分配 conv_id。

        Returns:
            写入后的 conv_id（首次创建时返回新分配的 id，更新时返回原 id）。
        """
        ...

    async def get(self, conv_id: str) -> Snapshot | None:
        """读取指定 conversation 的快照。"""
        ...

    async def append(self, conv_id: str, messages: list[dict]) -> None:
        """增量追加消息（不触碰已有行）。

        取消破坏性裁剪后消息只增，全量 put 在群聊高频下退化为 O(n²)。
        append 仅 INSERT 新行（sequence 续 max+1），写 message_stream_id/entry_type 列。
        全量 put 保留给"新建 session"和未来"摘要重写"。
        """
        ...

    async def delete(self, conv_id: str) -> None:
        """删除指定 conversation 及其数据。"""
        ...


# ── Run Result (new) ──────────────────────────────────


@dataclass
class ConversationRunResult:
    """Conversation.run() 返回值 — T3 新结构"""

    final_text: str = ""
    final_reason: str = ""  # "stop" | "max_rounds" | "error" | "empty_response" | ...
    delivery_performed: bool = False
    new_messages: list[dict] = field(default_factory=list)  # 本轮增量消息（message_delta）
    run_id: str = ""
    interaction_id: str = ""
    completion_kind: str = ""  # "completed" | "limit_reached" | "failed"
    output_arguments: dict | None = None  # T4: AgentRunResult.output.arguments 透传
    output_call_index: int | None = None  # T5: output 对应的 tool call index，chat final 保序用




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

    system prompt 不进 _messages——每次 run() 时由调用方显式传入。

    持久化：
    - save() / open() / delete() 通过 Store 协议操作
    - 懒恢复: 首次操作时从 store 加载快照
    - compact(): 原地压缩（摘要旧消息 + 保留近期消息）

    变更通知事务性：
    - register() 注册 ChangeSource
    - fetch_notifications() 拉取通知（纯读）
    - run() 成功后将通知 context 持久化到 _messages 并 apply cursor
    - run() 失败时不提交通知 context，不推进 cursor

    T3 重构：
    - system_prompt 不再持久化；每次 run() 由调用方显式传入
    - run() 走 AgentRuntime.run(AgentRunRequest) 新路径
    - _runtime 替代旧 _tool_loop
    """

    def __init__(self, store: Optional[Store] = None,
                 runtime: Optional[Any] = None,
                 stream_loader: Optional[StreamLoader] = None) -> None:
        self._store = store
        self._id: str | None = None
        self._messages: List[dict] = []
        self._change_sources: List[ChangeSource] = []
        self._cursors: dict[str, Any] = {}
        self._runtime = runtime  # AgentRuntime | None — T3 新路径
        # 引用展开器：按 message_stream_id 批量取回权威正文。
        # Life 路径无 ref 条目 → 永不触发，行为不变。
        self._stream_loader = stream_loader
        # 把「内存追加 + 对应增量落盘」视为一次提交。run() 与
        # append_ref() 可以并发；若两者分别在内存追加后竞争 Store
        # 锁，DB sequence 可能与 _messages 顺序不同。此锁只覆盖成功后的短
        # 提交段，不覆盖 LLM 调用，因此旁观消息仍可在 run 期间进入。
        self._commit_lock = asyncio.Lock()

    # ── ChangeSource 管理 ───────────────────────────────────────

    def register(self, source: ChangeSource) -> None:
        """注册变更来源。

        按 source_id 幂等：同 id 重复注册会替换 source 对象，但保留已有 cursor
        （避免破坏 Conversation.open() 恢复的 cursor 状态）。
        注册后按 (priority, source_id) 排序，保证通知注入顺序稳定。
        """
        # 幂等：移除同 source_id 的旧条目（替换 source 对象，保留 cursor）
        self._change_sources = [
            s for s in self._change_sources if s.source_id != source.source_id
        ]
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

    def add_message(self, role: str, content: str | list[dict]) -> None:
        """追加一条消息。支持纯文本和多模态 content parts。"""
        self._messages.append({"role": role, "content": content})

    def add_messages(self, new_messages: List[dict]) -> None:
        """追加一批消息至 _messages。

        system 角色消息不进 _messages——由 Agent 单独持有，render() 时拼接。
        [系统指令] 前缀的纠正消息会进入 _messages（有意保留，
        LLM 通过 system prompt 中的 SYS_INSTRUCTION_NOTICE 理解其含义）。
        """
        for msg in new_messages:
            self._messages.append(msg)

    # ── 引用条目与增量持久化──────────────────────────

    async def append_ref(self, message_stream_id: int, role: str) -> None:
        """追加一条对 message_stream 的可见引用条目并增量落盘。

        用于消息接入（群聊/私聊用户消息、送达的 assistant 消息）：正文由
        message_stream 权威保存，Conversation 只存引用，render 时展开。
        """
        entry = {
            "role": role,
            "entry_type": ENTRY_TYPE_REF,
            "message_stream_id": int(message_stream_id),
        }
        async with self._commit_lock:
            self._messages.append(entry)
            await self._persist_new([entry])

    async def _persist_new(self, new_entries: list[dict]) -> None:
        """增量持久化本次新增条目。

        - store 为 None（纯内存 Life 路径）→ 跳过。
        - 尚未分配 conv_id（session 未创建）→ 退回全量 save() 创建并写全部。
        - 已有 conv_id → 仅 append 新行，避免全量 DELETE+INSERT 的 O(n²)。
        """
        if self._store is None:
            return
        if self._id is None:
            await self.save()
            return
        if new_entries:
            await self._store.append(self._id, new_entries)

    async def render_resolved(self, system_prompt: str) -> List[dict]:
        """render 的引用展开版：ref 条目按 message_stream_id 取回正文后拼装。

        展开时把 message_stream 的 display_name 注入 OpenAI 原生 `name`
        字段来标识说话者（每条 ref 独立携带自己的说话者，非全局锚点；正文 content
        不含名字，避免"首个名字锚定"）。display_name 为空时不加 name。
        悬空引用（正文已被清除）兜底为 DANGLING_REF_FALLBACK。
        内部自有条目（通知/工具/摘要）原样保留。
        """
        ref_ids = [
            m["message_stream_id"]
            for m in self._messages
            if m.get("entry_type") == ENTRY_TYPE_REF and m.get("message_stream_id") is not None
        ]
        loaded: Dict[int, Any] = {}
        if ref_ids and self._stream_loader is not None:
            try:
                loaded = await self._stream_loader(ref_ids)
            except Exception:
                logger.warning(
                    "Conversation.render_resolved: stream_loader 失败，引用条目将兜底",
                    exc_info=True,
                )
                loaded = {}

        rendered: List[dict] = [{"role": "system", "content": system_prompt}]
        for m in self._messages:
            if m.get("entry_type") == ENTRY_TYPE_REF:
                msid = m.get("message_stream_id")
                record = loaded.get(msid) if msid is not None else None
                content = getattr(record, "content", None) if record is not None else None
                if content is None:
                    content = DANGLING_REF_FALLBACK
                msg: dict = {"role": m.get("role", "user"), "content": content}
                display_name = getattr(record, "display_name", "") if record is not None else ""
                if display_name:
                    # 说话者身份走 OpenAI 原生 name 字段，不进 content 正文；
                    # display_name 不可信（用户可控昵称），注入前净化，净化后为空则省略。
                    safe_name = _sanitize_speaker_name(display_name)
                    if safe_name:
                        msg["name"] = safe_name
                rendered.append(msg)
            else:
                rendered.append(dict(m))
        return rendered

    # ── 执行模板（新路径 T3）───────────────────────────────────────

    async def run(
        self,
        *,
        system_prompt: str,
        user_input: str | list[dict],
        interaction_id: str,
        tools: ToolKit | None = None,
        output: OutputSpec | None = None,
        selection: SelectionPolicy = CHAT,
        limits: LoopLimits | None = None,
        run_tag: str = "",
        agent_name: str = "",
        user_id: str = "",
        group_id: str = "",
        transient_context_messages: list[dict] | None = None,
        record_user_input: bool = True,
        token_budget: int = 0,
    ) -> ConversationRunResult:
        """T3 新执行模板：fetch → render → AgentRuntime.run() → commit/save。

        system_prompt 是每次 run 的显式输入，不持久化到 Conversation。

        职责顺序：
        1. fetch notifications（纯读）
        2. render messages: system_prompt + history + pending context + user_input + transient
        3. token 预算检查（Stage B 硬轮换）— 在 _runtime.run 前判定
        4. 组装 AgentRunRequest
        5. 调用 AgentRuntime.run(request)
        6. runtime 成功后：
           - append pending notification context 到 _messages
           - apply notification cursors
           - append user_input 到 _messages
           - append result.message_delta 到 _messages
           - save conversation
        7. runtime 失败时：
           - 不提交 pending notification context
           - 不 apply cursor
           - 不保存本轮 user_input / message_delta

        message_delta 语义：
        - Runtime 返回的 message_delta 不包含本轮 user_input
        - Conversation 自己负责追加 user_input，再追加 result.message_delta

        transient_context_messages：
        - 只用于本轮 LLM 输入，不保存进 Conversation 历史

        record_user_input：
        - True（默认，Life 路径）：user_input 注入本轮 render，成功后作为 own 条目
          追加到 _messages 并落盘。
        - False（chat 路径）：user_input 已由消息接入 hook 写入 message_stream 并
          以 ref 条目 append 进本 Conversation（在 run 之前），render_resolved 已包含它。
          故本轮不再重复注入 user_input，成功后也不再追加，避免重复与 few-shot 锚点。
          此时序由入站 hook（`command._inbound_message_recorder`，经
          `add_inbound_message_hook` 注册）早于命令派发触发保证；Conversation 自身
          无法校验该跨组件不变量，阶段 2 调整 hook 注册时机或 chat_command 路径时须维持它。

        limit_reached 处理策略：
        - limit_reached 视为未完成，不提交任何状态变更。
          对于 collect 模式（必须调用指定工具），这防止了不完整结果被持久化。
          对于 chat 模式（未来），可能需要单独评估部分输出的保留策略。

        token_budget：
        - >0 时在 _runtime.run 前逐 content estimate_tokens 累加。
        - 超出 token_budget 时立即返回 rotation_needed，不持久化本轮任何内容。
        - =0 时跳过检查（兼容 Life 路径）。
        """
        _runtime = self._runtime
        if _runtime is None:
            return ConversationRunResult(
                final_reason="error: no runtime",
                completion_kind="failed",
            )

        _limits = limits or LoopLimits()

        # 1. fetch — 纯读，不改变状态
        notifs, pending_cursors = await self.fetch_notifications()

        # 2. render — 拼装完整消息（引用条目按 message_stream_id 展开）
        messages = await self.render_resolved(system_prompt)
        # pending notification context 作为持久上下文注入（成功后保存）
        pending_persistent: list[dict] = [n.to_message() for n in notifs]
        messages.extend(pending_persistent)
        # chat 路径 user_input 已在 render_resolved 历史末尾（ref），不重复注入
        if record_user_input:
            messages.append({"role": "user", "content": user_input})
        if transient_context_messages:
            messages.extend(transient_context_messages)

        # Stage B 硬轮换 — _runtime.run 前 token 预算检查
        # 逐消息累加 content 的 estimate_tokens，并计入 role/name/tool_calls 的结构开销
        # （每条消息固定 +4 token 的角色/分隔开销 + tool_calls 的 JSON 长度估算），
        # 使估算不低于真实用量，避免边界配置下软超窗。
        # 此时 _runtime.run 尚未调用，无 LLM 调用、无持久化、幂等安全。
        if token_budget > 0:
            messages_total = 0
            for m in messages:
                messages_total += _MESSAGE_TOKEN_OVERHEAD
                content = m.get("content", "")
                if isinstance(content, str):
                    messages_total += estimate_tokens(content)
                elif isinstance(content, list):
                    for p in content:
                        if isinstance(p, dict):
                            messages_total += estimate_tokens(p.get("text", ""))
                name = m.get("name")
                if isinstance(name, str) and name:
                    messages_total += estimate_tokens(name)
                tool_calls = m.get("tool_calls")
                if tool_calls:
                    messages_total += estimate_tokens(json.dumps(tool_calls, ensure_ascii=False))
            if messages_total > token_budget:
                return ConversationRunResult(
                    final_reason="rotation_needed",
                    completion_kind="completed",
                )

        # 3. 组装 AgentRunRequest
        request = AgentRunRequest(
            interaction_id=interaction_id,
            messages=messages,
            tools=tools or ToolKit(),
            output=output,
            selection=selection,
            limits=_limits,
            metadata=RunMetadata(agent_name=agent_name, run_tag=run_tag, user_id=user_id, group_id=group_id),
        )

        # 4. 调用 AgentRuntime.run(request)
        result: AgentRunResult = await _runtime.run(request)

        # 5. 成功 → 提交 notification context、apply cursor、增量保存消息
        if result.completion.kind == "completed":
            delta = list(result.message_delta)
            async with self._commit_lock:
                new_entries: list[dict] = []
                # append pending notification context（持久化到 _messages）
                for msg in pending_persistent:
                    self._messages.append(msg)
                    new_entries.append(msg)
                # apply cursor
                self.apply_notifications(notifs, pending_cursors)
                # append user_input（仅 record_user_input=True；chat 路径已由 hook 追加 ref）
                if record_user_input:
                    ui_entry = {"role": "user", "content": user_input}
                    self._messages.append(ui_entry)
                    new_entries.append(ui_entry)
                # 完整保存 Runtime 的因果历史，包括送达/输出类工具的调用与结果。
                # 实际成功送达的正文还会由 ChatAgent 追加 assistant ref；两者分别表达
                # Agent 的执行意图与外部世界实际观察到的消息，不能互相替代。
                for m in delta:
                    self._messages.append(m)
                    new_entries.append(m)
                # 内存追加与增量落盘同处 commit 临界区，保证重载顺序一致。
                await self._persist_new(new_entries)


            return ConversationRunResult(
                final_text=result.output.text if result.output else "",
                final_reason=result.completion.code,
                delivery_performed=False,
                new_messages=delta,
                run_id=result.run_id,
                interaction_id=result.interaction_id,
                completion_kind=result.completion.kind,
                output_arguments=(
                    dict(result.output.arguments)
                    if result.output and result.output.arguments
                    else None
                ),
                output_call_index=(
                    result.output.call_index
                    if result.output
                    else None
                ),
            )
        else:
            # 6. 失败 → 不提交 notification context，不 apply cursor，
            #    不保存本轮 user_input / message_delta
            return ConversationRunResult(
                final_text="",
                final_reason=result.completion.code,
                delivery_performed=False,
                new_messages=[],
                run_id=result.run_id,
                interaction_id=result.interaction_id,
                completion_kind=result.completion.kind,
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

    async def save(self) -> None:
        """持久化当前快照。

        store 为 None 时跳过（纯内存模式）。首次 save 由 Store 分配 conv_id。
        cursor 序列化由 json.dumps 校验——不可序列化的 cursor 会在此时抛出 TypeError。

        T3: system_prompt 不再持久化。
        """
        if self._store is None:
            return
        snapshot = Snapshot(
            # 当前所有 messages 的 content 均为 str；若未来支持多模态 list content，改用 copy.deepcopy
            messages=[dict(m) for m in self._messages],
            cursors=dict(self._cursors),
        )
        returned_id = await self._store.put(self._id or "", snapshot)
        # Store 实现层分配或确认 id；写回以保证后续操作使用同一 id
        if returned_id is not None:
            self._id = returned_id

    @classmethod
    async def open(cls, conv_id: str, store: Store, *,
                   runtime: Optional[Any] = None,
                   stream_loader: Optional[StreamLoader] = None) -> "Conversation":
        """从存储恢复 Conversation。不存在时创建新的空实例。

        创建后调用方可注册 ChangeSource，然后首次 run()
        将触发懒恢复从 store 加载消息和 cursor。

        T3: system_prompt 不再从 DB 恢复——每次 run() 由调用方显式传入。
        阶段 1: 可注入 runtime / stream_loader（供 ConversationRegistry 复用活跃期）。
        """
        conv = cls(store=store, runtime=runtime, stream_loader=stream_loader)
        conv._id = conv_id
        snapshot = await store.get(conv_id)
        if snapshot is not None:
            conv._messages = snapshot.messages
            conv._cursors = snapshot.cursors
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

        .. deprecated::
            引入不可变摘要（Summarizer 协议 + _ensure_summary_for_scope），
            compact 的破坏性原地替换与不可变摘要冲突。保留方法本体供阶段 3c
            Life 全面接管前使用； 将删除并替换为 registry.close + 摘要。

        Returns:
            生成的摘要文本（用于日志/调试）。
        """
        if keep_recent <= 0:
            logger.warning("compact: keep_recent <= 0，跳过压缩")
            return ""
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
        """截断旧消息，保留最近 N 条，并保持 tool_call/result 配对完整。

        从尾部向前保留最近 keep_recent 条消息。
        如果边界消息是 tool role，向前展开截取以包含其配对的 assistant 消息
        （通过 tool_call_id 匹配 assistant.tool_calls[].id），确保同一轮
        assistant 发起的 tool_call ↔ tool_result 不被打断。

        配对保护只做一步检查——因为 tool 消息连续排列，边界至多
        跨越一个 tool_result，其配对的 assistant 消息必然在边界之前。

        Edge cases:
        - 边界消息不是 tool role → 朴素截取，不做配对检查
        - 边界 tool 消息缺少 tool_call_id → 跳过（无法配对）
        - 未找到配对 assistant → 保持朴素截取（孤立的 tool 结果）
        - 截取边界为 0（保留全部）→ 跳过，无需截取
        """
        if keep_recent <= 0:
            self._messages.clear()
            return
        if keep_recent >= len(self._messages):
            return

        # 朴素尾部截取起始索引 = 保留部分的首条位置
        start = len(self._messages) - keep_recent

        # 配对保护：如果边界消息是 tool role，展开以包含配对的 assistant
        if start < len(self._messages) and self._messages[start].get("role") == "tool":
            tool_call_id = self._messages[start].get("tool_call_id")
            if tool_call_id:
                # 向前查找含匹配 tool_call_id 的 assistant 消息
                for i in range(start - 1, -1, -1):
                    msg = self._messages[i]
                    if msg.get("role") == "assistant":
                        tool_calls = msg.get("tool_calls", [])
                        if isinstance(tool_calls, list) and any(
                            tc.get("id") == tool_call_id
                            for tc in tool_calls
                            if isinstance(tc, dict)
                        ):
                            start = i  # 展开截取边界至配对 assistant
                            break

        self._messages = list(self._messages[start:])

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
