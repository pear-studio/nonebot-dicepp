"""工具注册表 — 按 domain 注册/查找/执行"""
from typing import Dict, List, Callable, Set, Any
from dataclasses import dataclass

from .context import ToolContext


@dataclass
class ToolDef:
    """工具定义"""

    name: str
    description: str
    parameters: dict  # JSON Schema

    def to_openai_format(self) -> Dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    """工具注册表 — 按 domain 注册，按需注入 LLM 调用"""

    def __init__(self):
        self._tools: Dict[str, ToolDef] = {}  # name → ToolDef
        self._executors: Dict[str, Callable] = {}  # name → async (args, ctx) → str
        self._domains: Dict[str, List[str]] = {}  # domain → [tool_names]

    def register(self, domain: str, tool: ToolDef, executor: Callable) -> None:
        """注册工具到指定域"""
        self._tools[tool.name] = tool
        self._executors[tool.name] = executor
        self._domains.setdefault(domain, []).append(tool.name)

    def get_definitions_for(self, *domains: str) -> List[Dict]:
        """获取指定域的工具定义列表 (OpenAI function calling 格式)"""
        names: Set[str] = set()
        for d in domains:
            names.update(self._domains.get(d, []))
        return [self._tools[n].to_openai_format() for n in names]

    def make_executor_for(self, *domains: str, ctx: ToolContext):
        """
        返回闭包，作为 LLMRouter.generate_with_tools 的 tool_executor 回调。

        所有 executor 签名统一: async (args: dict, ctx: ToolContext) -> str
        ctx 在运行时注入，executor 不持有任何外部 import。

        tool_calls 格式约定（由 LLMRouter 负责统一，无论模型厂商）:
        [{"id": str, "name": str, "arguments": str}]

        LLMRouter 内部将不同厂商的 tool call 响应标准化为以上格式后再调用
        tool_executor。ToolRegistry 不感知厂商差异。
        """
        import json

        names: Set[str] = set()
        for d in domains:
            names.update(self._domains.get(d, []))

        async def executor(tool_calls: List[Dict]) -> List[Dict]:
            results = []
            for tc in tool_calls:
                name = tc["name"]
                if name in names and name in self._executors:
                    args = json.loads(tc.get("arguments", "{}"))
                    result = await self._executors[name](args, ctx)
                    results.append({"tool_call_id": tc["id"], "content": str(result)})
            return results

        return executor
