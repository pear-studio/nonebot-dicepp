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

from nonebot.log import logger

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
from ..llm.selection import SelectionPolicy


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
    ) -> AgentRunResult:
        """执行一次 Agent run（chat 路径）。"""
        run_id = new_run_id()
        turn_id = new_turn_id()

        # 配额预检
        if self._router.quota_check_enabled and self._router.data_store:
            from ..wall_clock import persona_wall_now
            tz = self._router.config.timezone if self._router.config else "Asia/Shanghai"
            today = persona_wall_now(tz).strftime("%Y-%m-%d")
            count = await self._router.data_store.get_daily_usage(user_id, today)
            if count >= self._router.daily_limit:
                raise QuotaExceeded(f"今日额度已用完 ({count}/{self._router.daily_limit})")

        # 初始化 state
        state = AgentRunState(
            run_id=run_id,
            turn_id=turn_id,
            user_id=user_id,
            group_id=group_id,
            mode="segmented_chat",
        )

        # 事件存储
        event_store = EventStore(self._store)

        # 事件总线 + sinks
        usage_sink = UsageSink(self._router)
        summary_sink = RunSummarySink(event_store)

        sinks = [usage_sink, summary_sink]
        bus = AgentEventBus(event_store=event_store, sinks=sinks)

        # action sinks
        delivery_sink = DeliverySink(port=self._port, store=self._store) if self._port else None
        image_sink = ImageGenerationSink(router=self._router)

        # LLMGateway
        gateway = LLMGateway(router=self._router, event_bus=bus)

        # ToolExecutor
        executor = ToolExecutor(registry=tool_registry, event_bus=bus)

        # Loop
        loop = AgentLoop(
            llm_gateway=gateway,
            tool_executor=executor,
            event_bus=bus,
            delivery_sink=delivery_sink,
            image_sink=image_sink,
            limits=self._limits,
        )

        # 写 persona_agent_runs
        await event_store.write_run(
            run_id=run_id, turn_id=turn_id,
            user_id=user_id, group_id=group_id,
            mode="segmented_chat",
        )

        # 事件：RunStarted
        await bus.emit(
            "AgentRunStarted",
            AgentRunStartedPayload(
                run_id=run_id, turn_id=turn_id,
                user_id=user_id, group_id=group_id,
                mode="segmented_chat",
            ),
            state,
        )

        # 工具定义
        tool_defs = tools or tool_registry.get_openai_schemas()

        # 执行 loop
        result = await loop.run(
            messages=messages,
            state=state,
            tools=tool_defs,
            tool_use_mode=ToolUseMode.REQUIRED_ONE_OF,
            required_tools=["send_reply_segment"],
            temperature=temperature,
            timeout=timeout,
            selection=SelectionPolicy.CHAT,
        )

        return result

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
        selection: SelectionPolicy = SelectionPolicy.SCORING,
        temperature: Optional[float] = None,
        timeout: Optional[int] = None,
    ) -> AgentRunResult:
        """执行一次 Agent run（通用路径，供 scoring/life 等非 chat 场景使用）。

        与 run_chat 的区别：
        - 默认使用 REQUIRED_ONE_OF 和 SCORING 策略
        - 不设置 DeliverySink / ImageGenerationSink
        - 不执行配额预检（配额由调用方控制）
        """
        run_id = new_run_id()
        turn_id = new_turn_id()

        # 初始化 state
        state = AgentRunState(
            run_id=run_id,
            turn_id=turn_id,
            user_id=user_id,
            group_id=group_id,
            mode=mode,
        )

        # 事件存储
        event_store = EventStore(self._store)

        # 事件总线 + sinks
        usage_sink = UsageSink(self._router)
        summary_sink = RunSummarySink(event_store)

        sinks = [usage_sink, summary_sink]
        bus = AgentEventBus(event_store=event_store, sinks=sinks)

        # LLMGateway
        gateway = LLMGateway(router=self._router, event_bus=bus)

        # ToolExecutor
        executor = ToolExecutor(registry=tool_registry, event_bus=bus)

        # Loop（通用路径不需要 DeliverySink / ImageGenerationSink）
        loop = AgentLoop(
            llm_gateway=gateway,
            tool_executor=executor,
            event_bus=bus,
            limits=self._limits,
        )

        # 写 persona_agent_runs
        await event_store.write_run(
            run_id=run_id, turn_id=turn_id,
            user_id=user_id, group_id=group_id,
            mode=mode,
        )

        # 事件：RunStarted
        await bus.emit(
            "AgentRunStarted",
            AgentRunStartedPayload(
                run_id=run_id, turn_id=turn_id,
                user_id=user_id, group_id=group_id,
                mode=mode,
            ),
            state,
        )

        # 工具定义
        tool_defs = tools or tool_registry.get_openai_schemas()

        # 执行 loop
        result = await loop.run(
            messages=messages,
            state=state,
            tools=tool_defs,
            tool_use_mode=tool_use_mode,
            required_tools=required_tools,
            temperature=temperature,
            timeout=timeout,
            selection=selection,
        )

        return result

    @staticmethod
    def build_tool_registry(
        old_registry: OldToolRegistry,
        domains: List[str],
        tool_ctx: Optional[ToolContext] = None,
    ) -> ToolRegistry:
        """从旧 ToolRegistry 构建新 ToolRegistry（迁移期桥梁）。"""
        return build_registry(old_registry, domains, ctx=tool_ctx)
