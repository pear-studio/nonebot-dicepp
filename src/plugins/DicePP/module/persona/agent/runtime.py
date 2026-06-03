"""AgentRuntime — Agent run 的装配和生命周期入口

一次 Agent run 的流程：
1. 创建 turn_id 和 agent_run_id
2. 初始化 AgentRunState
3. 组装 AgentEventBus、AgentLoop、LLMGateway、ToolExecutor、sinks
4. 配额预检
5. 写 persona_agent_runs
6. 调用 AgentLoop.run()
7. 更新 persona_agent_runs
8. 返回 AgentRunResult
"""
from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from utils.logger import logger

from ..data.store import PersonaDataStore
from ..gateway.port import MessagePort
from ..llm.router import LLMRouter, QuotaExceeded
from ..tools.registry import ToolRegistry as OldToolRegistry
from ..tools.context import ToolContext

from .event_bus import AgentEventBus, EventStore
from .events import AgentRunStartedPayload, AgentRunFinishedPayload
from .llm_gateway import LLMGateway
from .loop import AgentLoop, AgentRunResult
from .request import AgentRunLimits, ToolUseMode
from .sinks import DeliverySink, ImageGenerationSink, UsageSink, RunSummarySink
from .state import AgentRunState
from .tool_executor import ToolExecutor, ToolRegistry
from .tool_bridge import build_registry
from ..llm.selection import SelectionPolicy, CHAT, CHAT_WITH_IMAGE, SCORING


def new_run_id() -> str:
    return uuid.uuid4().hex[:24]


def new_turn_id() -> str:
    return uuid.uuid4().hex


class AgentRuntime:
    """Agent Runtime 装配入口"""

    def __init__(
        self,
        router: LLMRouter,
        store: PersonaDataStore,
        port: Optional[MessagePort] = None,
        limits: Optional[AgentRunLimits] = None,
    ) -> None:
        self._router = router
        self._store = store
        self._port = port
        self._limits = limits or AgentRunLimits()

    async def run_chat(
        self,
        messages: List[dict],
        user_id: str,
        group_id: str,
        tool_registry: ToolRegistry,
        *,
        tools: Optional[List[dict]] = None,
        temperature: Optional[float] = None,
        timeout: Optional[int] = None,
        image_data_urls: Optional[List[str]] = None,
    ) -> AgentRunResult:
        """执行一次 Agent run（chat 路径）。"""
        if self._router.quota_check_enabled and self._router.data_store:
            exempt = await self._is_quota_exempt(user_id, group_id)
            if not exempt:
                from utils.time import wall_now
                tz = self._router.config.timezone if self._router.config else "Asia/Shanghai"
                today = wall_now(tz).strftime("%Y-%m-%d")
                count = await self._router.data_store.get_daily_usage(user_id, today)
                if count >= self._router.daily_limit:
                    raise QuotaExceeded(f"今日额度已用完 ({count}/{self._router.daily_limit})")

        has_images = bool(image_data_urls)
        selection = CHAT_WITH_IMAGE if has_images else CHAT

        return await self._run_internal(
            messages=messages, user_id=user_id, group_id=group_id,
            tool_registry=tool_registry, mode="segmented_chat",
            tools=tools, tool_use_mode=ToolUseMode.AUTO,
            required_tools=None,
            selection=selection,
            temperature=temperature, timeout=timeout,
            bill_usage=True, with_action_sinks=True,
            image_data_urls=image_data_urls,
        )

    async def _is_quota_exempt(self, user_id: str, group_id: str) -> bool:
        """检查用户/群是否在白名单中，豁免配额限制。"""
        if not self._router.config or not self._router.data_store:
            return False
        if not getattr(self._router.config, "whitelist_enabled", False):
            return False
        if group_id and await self._router.data_store.is_group_whitelisted(group_id):
            return True
        if await self._router.data_store.is_user_whitelisted(user_id):
            return True
        return False

    async def run(
        self,
        messages: List[dict],
        user_id: str,
        group_id: str,
        tool_registry: ToolRegistry,
        *,
        mode: str = "agent",
        tools: Optional[List[dict]] = None,
        tool_use_mode: ToolUseMode = ToolUseMode.REQUIRED_ONE_OF,
        required_tools: Optional[List[str]] = None,
        selection: SelectionPolicy = SCORING,
        temperature: Optional[float] = None,
        timeout: Optional[int] = None,
        bill_usage: bool = False,
    ) -> AgentRunResult:
        """执行一次 Agent run（通用路径，供 scoring/life 等非 chat 场景使用）。

        与 run_chat 的区别：
        - 默认使用 REQUIRED_ONE_OF 和 SCORING 策略
        - 不设置 DeliverySink / ImageGenerationSink
        - 不执行配额预检（配额由调用方控制）
        - bill_usage=False 时不挂载 UsageSink，背景任务不计入用量
        """
        return await self._run_internal(
            messages=messages, user_id=user_id, group_id=group_id,
            tool_registry=tool_registry, mode=mode,
            tools=tools, tool_use_mode=tool_use_mode,
            required_tools=required_tools, selection=selection,
            temperature=temperature, timeout=timeout,
            bill_usage=bill_usage, with_action_sinks=False,
        )

    async def _run_internal(
        self,
        messages: List[dict],
        user_id: str,
        group_id: str,
        tool_registry: ToolRegistry,
        *,
        mode: str,
        tools: Optional[List[dict]],
        tool_use_mode: ToolUseMode,
        required_tools: Optional[List[str]],
        selection: SelectionPolicy,
        temperature: Optional[float],
        timeout: Optional[int],
        bill_usage: bool,
        with_action_sinks: bool,
        image_data_urls: Optional[List[str]] = None,
    ) -> AgentRunResult:
        """公共装配逻辑：创建 state/event_store/bus/gateway/executor/loop，执行并返回。"""
        run_id = new_run_id()
        turn_id = new_turn_id()

        state = AgentRunState(
            run_id=run_id, turn_id=turn_id,
            user_id=user_id, group_id=group_id, mode=mode,
        )

        event_store = EventStore(self._store)

        summary_sink = RunSummarySink(event_store)
        sinks: List = [summary_sink]
        if bill_usage:
            sinks.insert(0, UsageSink(self._router))
        bus = AgentEventBus(event_store=event_store, sinks=sinks)

        gateway = LLMGateway(router=self._router, event_bus=bus)
        executor = ToolExecutor(registry=tool_registry, event_bus=bus)

        delivery_sink = None
        image_sink = None
        if with_action_sinks:
            delivery_sink = DeliverySink(port=self._port, store=self._store) if self._port else None
            image_sink = ImageGenerationSink(router=self._router)

        loop = AgentLoop(
            llm_gateway=gateway, tool_executor=executor, event_bus=bus,
            delivery_sink=delivery_sink, image_sink=image_sink,
            limits=self._limits,
        )

        await event_store.write_run(
            run_id=run_id, turn_id=turn_id,
            user_id=user_id, group_id=group_id, mode=mode,
        )

        await bus.emit(
            "AgentRunStarted",
            AgentRunStartedPayload(
                run_id=run_id, turn_id=turn_id,
                user_id=user_id, group_id=group_id, mode=mode,
            ),
            state,
        )

        tool_defs = tools or tool_registry.get_openai_schemas()

        # T3: 当前消息带图 → 将图片嵌入最后一条 user 消息
        if image_data_urls:
            messages = _embed_images_in_last_user_message(messages, image_data_urls)

        return await loop.run(
            messages=messages, state=state, tools=tool_defs,
            tool_use_mode=tool_use_mode, required_tools=required_tools,
            temperature=temperature, timeout=timeout, selection=selection,
        )

    @staticmethod
    def build_tool_registry(
        old_registry: OldToolRegistry,
        domains: List[str],
        tool_ctx: Optional[ToolContext] = None,
    ) -> ToolRegistry:
        """从旧 ToolRegistry 构建新 ToolRegistry（迁移期桥梁）。"""
        return build_registry(old_registry, domains, ctx=tool_ctx)


def _build_image_content_parts(text: str, data_urls: List[str]) -> List[dict]:
    """构建多模态 content parts：text + image_url 列表。"""
    parts: List[dict] = [{"type": "text", "text": text}]
    for url in data_urls:
        parts.append({"type": "image_url", "image_url": {"url": url}})
    return parts


def _embed_images_in_last_user_message(
    messages: List[dict], image_data_urls: List[str],
) -> List[dict]:
    """将图片嵌入最后一条 user 消息（T3 流程：当前消息带图）。

    将最后一条 user 消息的 content 从 str 转为 List[dict]（多模态 parts）。
    """
    result = []
    for i, msg in enumerate(messages):
        if i == len(messages) - 1 and msg.get("role") == "user":
            text = msg.get("content", "")
            parts = _build_image_content_parts(text, image_data_urls)
            result.append({**msg, "content": parts})
        else:
            result.append(msg)
    return result
