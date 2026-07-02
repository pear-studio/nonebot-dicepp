"""ToolLoop — 统一的 LLM + 工具执行入口

包装 AgentRuntime，对外只暴露 execute(messages, config) → ToolResult。
替代当前 AgentRuntime.run_chat() / AgentRuntime.run() 双 API。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from ..agent.runtime import AgentRuntime
from ..agent.request import AgentRunLimits, ToolUseMode
from ..llm.router import LLMRouter
from ..data.store import PersonaDataStore
from ..gateway.port import MessagePort
from ..tools.registry import ToolRegistry as OldToolRegistry
from ..llm.selection import SelectionPolicy, CHAT, EVENT_GEN

from .conversation import RunConfig


@dataclass
class ToolResult:
    """ToolLoop.execute() 返回值"""

    new_messages: list[dict] = field(default_factory=list)
    final_text: str = ""
    final_reason: str = ""  # "stop" | "tool_called" | "max_rounds" | "error"
    delivery_performed: bool = False


class ToolLoop:
    """统一的 LLM + 工具执行器。

    调用 AgentRuntime 的公共 API，根据 RunConfig.mode 选择路径。
    不感知 Conversation。
    """

    def __init__(
        self,
        router: LLMRouter,
        store: PersonaDataStore,
        port: Optional[MessagePort] = None,
        limits: Optional[AgentRunLimits] = None,
    ) -> None:
        self._runtime = AgentRuntime(
            router=router, store=store, port=port, limits=limits,
        )

    async def execute(
        self, messages: list[dict], config: RunConfig,
    ) -> ToolResult:
        """执行一次 LLM 调用 + 工具循环。

        Args:
            messages: 完整消息列表（含 system prompt + 通知 + 用户输入）
            config: 执行配置

        Returns:
            ToolResult: 含增量消息和结果信息
        """
        # 构建 tool_registry（简化：调用方通过 config 传递，或使用旧接口）
        # 当前过渡阶段直接调用 AgentRuntime 现有 API
        sent_len = len(messages)

        if config.mode == "chat":
            result = await self._runtime.run_chat(
                messages=messages,
                user_id="", group_id="",
                tool_registry=None,  # type: ignore[arg-type]
                temperature=config.temperature,
                timeout=config.timeout,
            )
        else:
            # collect / react 等模式
            result = await self._runtime.run(
                messages=messages,
                user_id="", group_id="",
                tool_registry=None,  # type: ignore[arg-type]
                mode=config.mode,
                tools=config.tools or [],
                tool_use_mode=(
                    ToolUseMode.REQUIRED_ONE_OF
                    if config.mode == "collect"
                    else ToolUseMode.AUTO
                ),
                required_tools=None,
                selection=EVENT_GEN if config.mode == "collect" else CHAT,
                temperature=config.temperature,
                timeout=config.timeout,
                bill_usage=False,
            )

        # 提取增量消息：AgentRunResult 由 AgentLoop 在 state.messages 中保持
        # 当前 AgentRuntime 不直接暴露增量，使用空列表作为过渡
        new_messages: list[dict] = []

        return ToolResult(
            new_messages=new_messages,
            final_text=result.final_text or "",
            final_reason=result.final_reason or "stop",
            delivery_performed=result.delivery_performed or False,
        )
