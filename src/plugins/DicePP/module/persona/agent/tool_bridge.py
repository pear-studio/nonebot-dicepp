"""工具桥接 — 将旧 ToolDef/executor 适配为新 ToolSpec

M1 过渡期间，旧工具通过此桥接注册到新 ToolRegistry。
M3 清理时，各工具应直接创建 ToolSpec 并注册到 agent.ToolRegistry。
"""
from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Optional, Type

from pydantic import BaseModel, Field
from typing import Literal

from ..tools.context import ToolContext
from ..tools.registry import ToolRegistry as OldToolRegistry
from .actions import EffectKind
from .tool_executor import ToolRegistry, ToolSpec


# ── Args Schema 定义 ────────────────────────────────────────────


class SearchPersonaArgs(BaseModel):
    keyword: str = Field(default="", description="搜索关键词")
    source: str = Field(default="all", description="搜索来源")
    days: int = Field(default=7, description="搜索天数")
    limit: int = Field(default=5, ge=1, le=20, description="结果数量")
    user_id: str = Field(default="", description="按用户ID过滤")


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


class SuggestActionArgs(BaseModel):
    action_idea: str = Field(..., description="行动灵感描述")


class SendReplySegmentArgs(BaseModel):
    content: str = Field(default="", description="该段回复文本")
    image_url: str = Field(default="", description="图片URL")
    delay_before: float = Field(default=1.0, ge=0, description="发送前等待秒数")
    phase: Literal["interim", "final"] = Field(default="final", description="分段阶段")


class GenerateImageArgs(BaseModel):
    prompt: str = Field(..., description="图片描述")


# ── 工具名 → args_schema 映射 ──────────────────────────────────

_ARGS_SCHEMA_MAP: Dict[str, Type[BaseModel]] = {
    "search_persona": SearchPersonaArgs,
    "roll_dice": RollDiceArgs,
    "list_query_databases": ListQueryDatabasesArgs,
    "search_knowledge": SearchKnowledgeArgs,
    "suggest_action": SuggestActionArgs,
    "send_reply_segment": SendReplySegmentArgs,
    "generate_image": GenerateImageArgs,
}

_EXTERNAL_TOOLS = {"send_reply_segment", "generate_image"}


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
        else:
            spec = _make_pure_spec(name, desc, args_schema, old_executor_fn)

        reg.register(spec)

    return reg


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
            else:
                fields[key] = (Opt[py_type], None)

    return BaseModel.create(name + "_Args", **fields)


def _json_type(typ: str) -> type:
    mapping = {
        "string": str, "integer": int, "number": float,
        "boolean": bool, "object": dict, "array": list,
    }
    return mapping.get(typ, str)
