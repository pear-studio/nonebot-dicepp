"""ToolLoop — 统一的 LLM + 工具执行入口

包装 AgentRuntime，对外只暴露 execute(messages, config) → ToolResult。
替代当前 AgentRuntime.run_chat() / AgentRuntime.run() 双 API。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from utils.logger import logger

from ..agent.runtime import AgentRuntime
from ..agent.request import AgentRunLimits, ToolUseMode
from ..llm.router import LLMRouter
from ..data.store import PersonaDataStore
from ..gateway.port import MessagePort
from ..agent.tool_executor import ToolRegistry as ToolRegistry
from ..llm.selection import SelectionPolicy, CHAT, EVENT_GEN

from .conversation import RunConfig


@dataclass
class ToolResult:
    """ToolLoop.execute() 返回值"""

    new_messages: list[dict] = field(default_factory=list)
    final_text: str = ""
    final_reason: str = ""
    delivery_performed: bool = False
    terminated_by: str = ""  # 终止工具名，如 "end_conversation"


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
        tool_registry: Optional[Any] = None,
    ) -> None:
        self._runtime = AgentRuntime(
            router=router, store=store, port=port, limits=limits,
        )
        self._tool_registry = tool_registry  # 可选工具注册表（查询类工具等）

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
        sent_len = len(messages)

        if config.mode == "chat":
            result = await self._runtime.run_chat(
                messages=messages,
                user_id="", group_id="",
                tool_registry=self._tool_registry,
                temperature=config.temperature,
                timeout=config.timeout,
                image_data_urls=config.image_data_urls,
            )
        else:
            # 构建工具注册表：必需工具的简单 executor + 可选工具
            tool_registry = _build_collect_registry(
                config.tools or [],
                required_tools=config.required_tools,
            )
            # 合并可选工具（如 search_story_deck）
            extra = config.tool_registry or self._tool_registry
            if extra is not None:
                for spec in extra.list_tools():
                    if tool_registry.get(spec.name) is None:
                        tool_registry.register(spec)

            result = await self._runtime.run(
                messages=messages,
                user_id="", group_id="",
                tool_registry=tool_registry,
                mode="structured_collect",
                tools=config.tools or [],
                tool_use_mode=(
                    ToolUseMode.REQUIRED_ONE_OF
                    if config.mode == "collect"
                    else ToolUseMode.AUTO
                ),
                required_tools=config.required_tools,
                selection=config.selection or (
                    EVENT_GEN if config.mode == "collect" else CHAT
                ),
                temperature=config.temperature,
                timeout=config.timeout,
                bill_usage=False,
            )

        # 运行时失败日志（替代旧 run_structured_collect 中的 log_if_failed）
        if result.final_reason and result.final_reason not in ("stop", "max_rounds", "direct_content"):
            logger.warning(
                f"ToolLoop.execute: 异常终止 mode={config.mode} "
                f"reason={result.final_reason} status={getattr(result, 'status', 'unknown')}"
            )

        # 提取增量消息
        all_msgs = result.final_messages if result.final_messages else []
        # 过滤纠正注入（[系统指令] 前缀）
        new_msgs = _filter_corrections(all_msgs[sent_len:])

        return ToolResult(
            new_messages=new_msgs,
            final_text=result.final_text or "",
            final_reason=result.final_reason or "stop",
            delivery_performed=result.delivery_performed or False,
            terminated_by=getattr(result, "terminated_by", "") or "",
        )


_CORRECTION_PREFIX = "[系统指令]"


def _filter_corrections(msgs: list[dict]) -> list[dict]:
    """过滤掉内部纠正注入消息。"""
    filtered = [
        m for m in msgs
        if not (
            m.get("role") == "user"
            and isinstance(m.get("content"), str)
            and m["content"].startswith(_CORRECTION_PREFIX)
        )
    ]
    removed = len(msgs) - len(filtered)
    if removed > 0:
        logger.debug(f"_filter_corrections: removed {removed} correction message(s)")
    return filtered


def _build_collect_registry(
    tools: list[dict],
    required_tools: list[str] | None = None,
) -> "ToolRegistry":
    """构建 collect 模式的工具注册表。

    为每个工具创建最简 executor（返回 "ok"），使 LLM 的工具调用能正常完成。
    工具调用参数保留在 LLM 返回的消息中，由调用方自行提取。

    当 tools 非空时从 tools 提取工具定义；为空时从 required_tools 和
    _ARGS_SCHEMA_MAP 构建。
    """
    from ..agent.tool_executor import ToolSpec
    from ..agent.actions import EffectKind
    from ..agent.tool_bridge import _ARGS_SCHEMA_MAP

    reg = ToolRegistry()
    required = set(required_tools or [])

    if tools:
        # 从 OpenAI 工具定义构建
        for tool_def in tools:
            func = tool_def.get("function", tool_def) if isinstance(tool_def, dict) else tool_def
            name = func.get("name", "") if isinstance(func, dict) else ""
            if not name:
                continue
            desc = func.get("description", "") if isinstance(func, dict) else ""

            args_schema = _ARGS_SCHEMA_MAP.get(name)
            if args_schema is None:
                logger.warning(
                    f"_build_collect_registry: 工具 '{name}' 的 args_schema "
                    f"未在 _ARGS_SCHEMA_MAP 中注册，已跳过"
                )
                continue

            async def _exec(**kwargs: Any) -> str:
                return "ok"

            spec = ToolSpec(
                name=name,
                description=desc,
                args_schema=args_schema,
                effect=EffectKind.PURE,
                executor=_exec,
            )
            reg.register(spec)
    else:
        # 无工具定义时，从 _ARGS_SCHEMA_MAP 为 required_tools 构建
        for name, model in _ARGS_SCHEMA_MAP.items():
            if required and name not in required:
                continue

            async def _exec(**kwargs: Any) -> str:
                return "ok"

            spec = ToolSpec(
                name=name,
                description=model.__doc__ or "",
                args_schema=model,
                effect=EffectKind.PURE,
                executor=_exec,
            )
            reg.register(spec)

    return reg


def _parse_tool_args(new_messages: list[dict], tool_name: str) -> list[dict]:
    """从 LLM 返回的 new_messages 中提取指定工具的调用参数。

    兼容 Anthropic 格式（content list 含 tool_use 块）和 OpenAI 格式（tool_calls）。
    """
    import json
    collected: list[dict] = []
    for msg in new_messages:
        if msg.get("role") != "assistant":
            continue
        # Anthropic 格式
        content = msg.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    if block.get("name") == tool_name:
                        inp = block.get("input", {})
                        if isinstance(inp, dict):
                            collected.append(inp)
        # OpenAI 格式
        elif isinstance(msg.get("tool_calls"), list):
            for tc in msg["tool_calls"]:
                func = tc.get("function", {})
                if func.get("name") == tool_name:
                    try:
                        args = json.loads(func.get("arguments", "{}"))
                    except (json.JSONDecodeError, TypeError):
                        args = {}
                    if isinstance(args, dict):
                        collected.append(args)
    return collected
