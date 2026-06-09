"""工具桥接 — 将旧 ToolDef/executor 适配为新 ToolSpec

M1 过渡期间，旧工具通过此桥接注册到新 ToolRegistry。
M3 清理时，各工具应直接创建 ToolSpec 并注册到 agent.ToolRegistry。
"""
from __future__ import annotations

import json
import re
from typing import Any, Awaitable, Callable, Dict, List, Literal, Optional, Type

from pydantic import BaseModel, Field, create_model

from ..tools.collecting import (
    RecordDiaryEntryArgs,
    RecordEventArgs,
    RecordReactionArgs,
    RecordScoreArgs,
    RecordShareMessageArgs,
)
from ..tools.context import ToolContext
from ..tools.registry import ToolRegistry as OldToolRegistry
from .actions import EffectKind
from .tool_executor import ToolRegistry, ToolSpec


# ── Args Schema 定义 ────────────────────────────────────────────


class RollDiceArgs(BaseModel):
    expression: str = Field(..., description="骰子表达式")


class ListQueryDatabasesArgs(BaseModel):
    pass  # 无参数


class SearchKnowledgeArgs(BaseModel):
    keyword: str = Field(default="", description="搜索关键词")
    tags: Optional[List[str]] = Field(default=None, description="标签过滤")
    category: str = Field(default="", description="分类过滤")
    source: str = Field(default="", description="来源过滤")
    query: str = Field(default="", description="原始查询字符串")
    database: str = Field(default="", description="资料库名称")
    limit: int = Field(default=5, ge=1, le=10, description="结果上限")
    fulltext: bool = Field(default=False, description="全文搜索")
    detail_index: Optional[int] = Field(default=None, description="获取完整内容")


class ReadHistoryArgs(BaseModel):
    limit: int = Field(default=10, ge=1, le=50, description="返回条数")
    offset: int = Field(default=0, ge=0, description="跳过前N条")
    user_id: str = Field(default="", description="按用户ID过滤")


class SearchHistoryArgs(BaseModel):
    keyword: str = Field(..., description="搜索关键词")
    limit: int = Field(default=10, ge=1, le=50, description="返回条数")
    days: int = Field(default=30, ge=1, le=365, description="搜索最近N天")
    user_id: str = Field(default="", description="按用户ID过滤")


class ReadProfileArgs(BaseModel):
    pass


class ReadDiaryArgs(BaseModel):
    days: int = Field(default=7, ge=1, le=365, description="读取最近N天")
    limit: int = Field(default=5, ge=1, le=30, description="最多返回篇数")


class SearchDiaryArgs(BaseModel):
    keyword: str = Field(..., description="搜索关键词")
    days: int = Field(default=30, ge=1, le=365, description="搜索最近N天")
    limit: int = Field(default=5, ge=1, le=20, description="最多返回条数")


class SuggestActionArgs(BaseModel):
    action_idea: str = Field(..., description="行动灵感描述")


class SendReplySegmentArgs(BaseModel):
    content: str = Field(default="", description="该段回复文本")
    image_url: str = Field(default="", description="图片URL")
    delay_before: float = Field(default=1.0, ge=0, description="发送前等待秒数")
    phase: Literal["interim", "final"] = Field(default="final", description="分段阶段")


class GenerateImageArgs(BaseModel):
    prompt: str = Field(..., description="图片描述")


class LookAtPastImageArgs(BaseModel):
    image_hash: str = Field(..., description="图片的 8 位十六进制标识，从上下文标记中复制", min_length=8, max_length=8)


# ── 旧 ScoringAgent / ActionEvaluator 工具 Args Schema（桥接兼容）──


class ScoreRelationshipArgs(BaseModel):
    """score_relationship 参数 — ScoringAgent 当前使用"""
    deltas: Dict[str, float] = Field(default_factory=dict, description="好感度变化，含 intimacy/reputation_delta")
    facts: Dict[str, Any] = Field(default_factory=dict, description="提取的用户事实")


class RecordEvaluationArgs(BaseModel):
    """记录行动可行性评估结果"""
    result: Literal["approved", "rejected", "deferred"] = Field(..., description="评估结果")
    reason: str = Field(default="", description="评估理由")


# ── 工具名 → args_schema 映射 ──────────────────────────────────

_ARGS_SCHEMA_MAP: Dict[str, Type[BaseModel]] = {
    "roll_dice": RollDiceArgs,
    "list_query_databases": ListQueryDatabasesArgs,
    "search_knowledge": SearchKnowledgeArgs,
    "read_history": ReadHistoryArgs,
    "search_history": SearchHistoryArgs,
    "read_profile": ReadProfileArgs,
    "read_diary": ReadDiaryArgs,
    "search_diary": SearchDiaryArgs,
    "suggest_action": SuggestActionArgs,
    "send_reply_segment": SendReplySegmentArgs,
    "generate_image": GenerateImageArgs,
    "look_at_past_image": LookAtPastImageArgs,
    # M2: 结构化采集工具
    "record_event": RecordEventArgs,
    "record_reaction": RecordReactionArgs,
    "record_diary_entry": RecordDiaryEntryArgs,
    "record_share_message": RecordShareMessageArgs,
    "record_score": RecordScoreArgs,
    # M2: 旧工具名桥接兼容
    "record_evaluation": RecordEvaluationArgs,
    "score_relationship": ScoreRelationshipArgs,
}

_EXTERNAL_TOOLS = {"send_reply_segment", "generate_image"}
_STATE_WRITE_TOOLS = {
    "record_event", "record_reaction", "record_diary_entry",
    "record_share_message", "record_score",
    "record_evaluation", "score_relationship",
}


def _make_pure_spec(
    name: str,
    desc: str,
    args_schema: Type[BaseModel],
    old_executor_fn: Callable,
) -> ToolSpec:
    """创建 PURE 工具的 ToolSpec（闭包安全版本）。"""

    async def _exec(**kwargs: Any) -> str:
        tc = [{
            "id": name,
            "name": name,
            "arguments": json.dumps(kwargs, ensure_ascii=False),
        }]
        results = await old_executor_fn(tc)
        return results[0]["content"] if results else ""

    return ToolSpec(
        name=name,
        description=desc,
        args_schema=args_schema,
        effect=EffectKind.PURE,
        executor=_exec,
    )


def _make_external_spec(
    name: str,
    desc: str,
    args_schema: Type[BaseModel],
) -> ToolSpec:
    """创建 EXTERNAL_ACTION 工具的 ToolSpec（executor 只返回参数 JSON）。"""

    async def _exec(**kwargs: Any) -> str:
        return json.dumps(kwargs, ensure_ascii=False)

    return ToolSpec(
        name=name,
        description=desc,
        args_schema=args_schema,
        effect=EffectKind.EXTERNAL_ACTION,
        executor=_exec,
    )


def _make_state_write_spec(
    name: str,
    desc: str,
    args_schema: Type[BaseModel],
    old_executor_fn: Callable,
) -> ToolSpec:
    """创建 STATE_WRITE 工具的 ToolSpec（包装旧 executor 闭包）。"""

    async def _exec(**kwargs: Any) -> str:
        tc = [{
            "id": name,
            "name": name,
            "arguments": json.dumps(kwargs, ensure_ascii=False),
        }]
        results = await old_executor_fn(tc)
        return results[0]["content"] if results else ""

    return ToolSpec(
        name=name,
        description=desc,
        args_schema=args_schema,
        effect=EffectKind.STATE_WRITE,
        executor=_exec,
    )


def build_registry(
    old_registry: OldToolRegistry,
    domains: List[str],
    ctx: Optional[ToolContext] = None,
) -> ToolRegistry:
    """从旧 ToolRegistry 构建新 ToolRegistry。

    PURE/STATE_WRITE 工具：包装旧 executor 闭包。
    EXTERNAL_ACTION 工具（send_reply_segment, generate_image）：
      只返回参数 JSON，由 AgentLoop 路由到对应 sink。
    """
    reg = ToolRegistry()
    old_schemas = old_registry.get_definitions_for(*domains)
    old_executor_fn = old_registry.make_executor_for(*domains, ctx=ctx)

    for schema in old_schemas:
        func = schema.get("function", schema)
        name = func.get("name", "")
        desc = func.get("description", "")
        params = func.get("parameters", {})
        args_schema = _ARGS_SCHEMA_MAP.get(name, _dynamic_model(name, params))

        if name in _EXTERNAL_TOOLS:
            spec = _make_external_spec(name, desc, args_schema)
        elif name in _STATE_WRITE_TOOLS:
            spec = _make_state_write_spec(name, desc, args_schema, old_executor_fn)
        else:
            spec = _make_pure_spec(name, desc, args_schema, old_executor_fn)

        reg.register(spec)

    return reg


# ── 结构化采集工具专用注册 ──────────────────────────────────────


_COLLECTING_MODELS: List[Type[BaseModel]] = [
    RecordEventArgs,
    RecordReactionArgs,
    RecordDiaryEntryArgs,
    RecordShareMessageArgs,
    RecordScoreArgs,
    RecordEvaluationArgs,
]


def _model_to_tool_name(model: Type[BaseModel]) -> str:
    """从 Pydantic model 类名推导工具名（PascalCase → snake_case，去 Args 后缀）。"""
    name = model.__name__
    if name.endswith("Args"):
        name = name[:-4]
    return re.sub(r'(?<=[a-z])([A-Z])', r'_\1', name).lower()


def build_collecting_registry(
    executor_fn: Callable[[Dict[str, Any]], Awaitable[str]],
    tool_names: list[str] | None = None,
) -> ToolRegistry:
    """构建结构化采集工具的新 ToolRegistry，所有工具标记为 STATE_WRITE。

    Args:
        executor_fn: 统一的收集型 executor，签名 async (args: dict) -> str。
                     由调用方提供（如包装 DB 写入或闭包收集）。
        tool_names: 限定注册的工具名列表；None 表示注册全部。

    Returns:
        新 ToolRegistry，包含指定的 STATE_WRITE 工具。
    """
    reg = ToolRegistry()

    for model in _COLLECTING_MODELS:
        name = _model_to_tool_name(model)
        if tool_names is not None and name not in tool_names:
            continue
        async def _exec(**kwargs: Any) -> str:
            return await executor_fn(kwargs)

        spec = ToolSpec(
            name=name,
            description=model.__doc__ or "",
            args_schema=model,
            effect=EffectKind.STATE_WRITE,
            executor=_exec,
        )
        reg.register(spec)

    return reg


async def run_structured_collect(
    router,
    store,
    messages: list,
    *,
    user_id: str = "",
    group_id: str = "",
    required_tools: list | None = None,
    temperature: float = 0.7,
    timeout: int | None = None,
    selection=None,
    max_tool_rounds: int = 1,
) -> tuple[list, Any]:
    """运行 structured_collect 模式，返回 (collected_args, runtime_result)。"""
    from .runtime import AgentRuntime
    from .request import AgentRunLimits

    collected: list = []

    async def _collect(args: dict) -> str:
        collected.append(args)
        return "ok"

    runtime = AgentRuntime(
        router=router,
        store=store,
        limits=AgentRunLimits(max_tool_rounds=max_tool_rounds),
    )
    tool_registry = build_collecting_registry(_collect, tool_names=required_tools)

    result = await runtime.run(
        messages=messages,
        user_id=user_id,
        group_id=group_id,
        tool_registry=tool_registry,
        required_tools=required_tools,
        temperature=temperature,
        timeout=timeout,
        selection=selection,
        mode="structured_collect",
    )
    return collected, result


def _dynamic_model(name: str, parameters: dict) -> Type[BaseModel]:
    """从 JSON schema 参数构建 Pydantic model（fallback）。"""
    from typing import Optional as Opt

    properties = parameters.get("properties", {})
    required = set(parameters.get("required", []))
    fields: Dict[str, tuple] = {}

    for key, prop in properties.items():
        py_type = _json_type(prop.get("type", "string"))
        if key in required:
            fields[key] = (py_type, Field(...))
        else:
            default = prop.get("default", None)
            if default is not None:
                fields[key] = (Opt[py_type], Field(default=default))
            elif py_type is list:
                fields[key] = (Opt[py_type], Field(default_factory=list))
            elif py_type is dict:
                fields[key] = (Opt[py_type], Field(default_factory=dict))
            else:
                fields[key] = (Opt[py_type], None)

    return create_model(name + "_Args", **fields)


def _json_type(typ: str) -> type:
    mapping = {
        "string": str, "integer": int, "number": float,
        "boolean": bool, "object": dict, "array": list,
    }
    return mapping.get(typ, str)
