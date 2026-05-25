"""ToolSpec / ToolExecutor — 新 Agent Runtime 的工具执行层

职责：
- 注册 ToolSpec
- 参数解析 + Pydantic 校验
- 执行 PURE / STATE_WRITE 工具
- 对 EXTERNAL_ACTION 工具返回 DeclaredAction
- 归一化错误，产生 tool events
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional, Type

from nonebot.log import logger
from pydantic import BaseModel, ValidationError

from .actions import EffectKind
from .event_bus import AgentEventBus
from .events import (
    DeclaredActionProducedPayload,
    ToolArgumentsInvalidPayload,
    ToolArgumentsValidatedPayload,
    ToolCallRequestedPayload,
    ToolExecutionCompletedPayload,
    ToolExecutionFailedPayload,
    ToolExecutionStartedPayload,
)
from .state import AgentRunState


@dataclass
class ToolSpec:
    """工具定义 — 替代旧 ToolDef"""

    name: str
    description: str
    args_schema: Type[BaseModel]
    effect: EffectKind
    executor: Callable[..., Awaitable[Any]]


class ToolRegistry:
    """新工具注册表 — 注册 ToolSpec，提供 schema 和查找"""

    def __init__(self) -> None:
        self._tools: Dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        self._tools[spec.name] = spec

    def get(self, name: str) -> Optional[ToolSpec]:
        return self._tools.get(name)

    def get_openai_schemas(self) -> List[dict]:
        """返回所有注册工具的 OpenAI function schema 列表。"""
        result = []
        for spec in self._tools.values():
            schema = spec.args_schema.model_json_schema()
            result.append({
                "type": "function",
                "function": {
                    "name": spec.name,
                    "description": spec.description,
                    "parameters": schema,
                },
            })
        return result

    def list_tools(self) -> List[ToolSpec]:
        return list(self._tools.values())


# ── ToolExecutor ────────────────────────────────────────────────


class ToolExecutor:
    """工具执行深接口 — 参数校验、执行、action 生成、错误归一化。"""

    def __init__(self, registry: ToolRegistry, event_bus: AgentEventBus) -> None:
        self._registry = registry
        self._event_bus = event_bus

    async def execute_many(
        self,
        tool_calls: List[dict],
        state: AgentRunState,
    ) -> List[dict]:
        """批量执行工具调用。

        Args:
            tool_calls: [{"id": ..., "name": ..., "arguments": json_str}, ...]
            state: 当前 run state

        Returns:
            tool_results: [{"tool_call_id": ..., "content": ...}, ...]
        """
        results: List[dict] = []
        for tc in tool_calls:
            result = await self._execute_one(tc, state)
            results.append(result)
        return results

    async def _execute_one(self, tc: dict, state: AgentRunState) -> dict:
        tc_id = tc["id"]
        tc_name = tc["name"]
        raw_args = tc.get("arguments", "{}")

        # 事件：ToolCallRequested
        await self._event_bus.emit(
            "ToolCallRequested",
            ToolCallRequestedPayload(
                round_index=state.tool_rounds,
                tool_call_id=tc_id,
                tool_name=tc_name,
                raw_arguments=raw_args,
            ),
            state,
        )

        spec = self._registry.get(tc_name)
        if spec is None:
            err_msg = f"工具 {tc_name} 未注册"
            await self._event_bus.emit(
                "ToolExecutionFailed",
                ToolExecutionFailedPayload(
                    tool_call_id=tc_id, tool_name=tc_name, error=err_msg,
                ),
                state,
            )
            return {"tool_call_id": tc_id, "content": err_msg}

        # 参数校验
        try:
            raw = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
        except json.JSONDecodeError as e:
            await self._event_bus.emit(
                "ToolArgumentsInvalid",
                ToolArgumentsInvalidPayload(
                    tool_call_id=tc_id, tool_name=tc_name, error=str(e),
                ),
                state,
            )
            return {"tool_call_id": tc_id, "content": f"参数解析失败: {e}"}

        try:
            parsed = spec.args_schema.model_validate(raw)
        except ValidationError as e:
            await self._event_bus.emit(
                "ToolArgumentsInvalid",
                ToolArgumentsInvalidPayload(
                    tool_call_id=tc_id, tool_name=tc_name, error=str(e),
                ),
                state,
            )
            return {"tool_call_id": tc_id, "content": f"参数校验失败: {e}"}

        await self._event_bus.emit(
            "ToolArgumentsValidated",
            ToolArgumentsValidatedPayload(tool_call_id=tc_id, tool_name=tc_name),
            state,
        )

        # EXTERNAL_ACTION 特殊处理
        if spec.effect == EffectKind.EXTERNAL_ACTION:
            action_id = uuid.uuid4().hex[:16]
            result = await spec.executor(**parsed.model_dump())
            await self._event_bus.emit(
                "DeclaredActionProduced",
                DeclaredActionProducedPayload(
                    tool_call_id=tc_id,
                    tool_name=tc_name,
                    action_type=_action_type(tc_name),
                    action_id=action_id,
                ),
                state,
            )
            # EXTERNAL_ACTION 返回非空 content 供 observation 回填
            return {"tool_call_id": tc_id, "content": str(result), "_action_id": action_id}

        # PURE / STATE_WRITE 直接执行
        await self._event_bus.emit(
            "ToolExecutionStarted",
            ToolExecutionStartedPayload(tool_call_id=tc_id, tool_name=tc_name),
            state,
        )

        try:
            content = await spec.executor(**parsed.model_dump())
        except Exception as e:
            logger.warning(f"工具 {tc_name} 执行失败: {e}")
            await self._event_bus.emit(
                "ToolExecutionFailed",
                ToolExecutionFailedPayload(
                    tool_call_id=tc_id, tool_name=tc_name, error=str(e),
                ),
                state,
            )
            return {"tool_call_id": tc_id, "content": f"工具执行失败: {e}"}

        await self._event_bus.emit(
            "ToolExecutionCompleted",
            ToolExecutionCompletedPayload(
                tool_call_id=tc_id, tool_name=tc_name, content=str(content),
            ),
            state,
        )

        return {"tool_call_id": tc_id, "content": str(content)}


def _action_type(tool_name: str) -> str:
    mapping = {
        "send_reply_segment": "send_message",
        "generate_image": "generate_image",
    }
    return mapping.get(tool_name, tool_name)
