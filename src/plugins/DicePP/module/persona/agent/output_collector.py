"""OutputCollector — 处理 OutputSpec 的结构化输出收集

职责：
- 接收匹配 output.name 的 tool_call。
- JSON parse + Pydantic validation。
- 返回 ToolResult（observation text + status）。
- 不执行副作用，不写 DB。
"""
from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ValidationError

from .runtime_types import OutputSpec, ToolResult


class OutputCollector:
    """收集并校验最终结构化输出"""

    def __init__(self, output_spec: OutputSpec) -> None:
        self._spec = output_spec

    @property
    def name(self) -> str:
        return self._spec.name

    def collect(self, raw_arguments: str) -> tuple[ToolResult, BaseModel | None]:
        """解析并校验 output 参数。

        Returns:
            (ToolResult, parsed_model | None)
            - 成功: ToolResult(status="success"), parsed model
            - 失败: ToolResult(status="error"), None
        """
        # JSON parse
        try:
            raw = json.loads(raw_arguments) if isinstance(raw_arguments, str) else raw_arguments
        except json.JSONDecodeError as e:
            return (
                ToolResult(
                    observation=f"输出参数解析失败: {e}",
                    status="error",
                ),
                None,
            )

        # Pydantic validation
        try:
            parsed = self._spec.args_schema.model_validate(raw)
        except ValidationError as e:
            return (
                ToolResult(
                    observation=f"输出校验失败: {e}",
                    status="error",
                ),
                None,
            )

        return (
            ToolResult(
                observation=self._spec.accepted_observation,
                status="success",
            ),
            parsed,
        )

    def get_openai_schema(self) -> dict:
        """返回 OutputSpec 的 OpenAI function schema（与 ToolKit.get_openai_schemas 格式一致）"""
        schema = self._spec.args_schema.model_json_schema()
        return {
            "type": "function",
            "function": {
                "name": self._spec.name,
                "description": self._spec.description,
                "parameters": schema,
            },
        }

    def build_args_dict(self, parsed: BaseModel) -> dict[str, Any]:
        """从解析后的 model 提取 arguments dict"""
        return parsed.model_dump()
