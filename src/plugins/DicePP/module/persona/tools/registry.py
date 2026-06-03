"""工具注册表 — 按 domain 注册/查找/执行"""
import json
from utils.logger import logger
from typing import Dict, List, Callable, Set, Any
from dataclasses import dataclass

from .context import ToolContext


class ToolDomain:
    """工具域常量"""

    CHAT = "chat"
    LIFE = "life"


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
    """工具注册表 — 按 domain 注册，按需注入 LLM 调用

    Domain 语义
    -----------
    domain 是一个**调用上下文标签**，用于回答："这个工具应该在什么场景被
    LLM 看到？"目前的取值：

      - ``chat``  ：用户消息处理路径（``ChatSession._chat_with_tools``）
        可注入的工具集；典型成员有 ``search_persona``、``search_knowledge``、
        ``roll_dice``——它们让模型在回复用户时能查档案、查历史、查规则、掷骰。
      - 未来可能扩展 ``life`` / ``proactive`` 等域，区分主动事件路径下
        允许的工具子集。

    一个工具可以注册到多个域（例如某天 ``roll_dice`` 也想给 life 用），
    但目前每个工具只在 ``chat`` 域。

    Domain 不是权限边界——执行时不会再校验调用方是否"属于该 domain"，
    它只是 *声明* 哪些工具会被打包给某条 LLM 路径。权限/安全检查应在
    executor 内部完成。
    """

    def __init__(self):
        self._tools: Dict[str, ToolDef] = {}  # name → ToolDef
        self._executors: Dict[str, Callable] = {}  # name → async (args, ctx) → str
        self._domains: Dict[str, List[str]] = {}  # domain → [tool_names]

    def register(self, domain: str, tool: ToolDef, executor: Callable) -> None:
        """注册工具到指定域"""
        if tool.name in self._tools:
            existing_domains = [d for d, names in self._domains.items() if tool.name in names]
            logger.warning(
                f"工具 {tool.name} 已在域 {existing_domains} 注册，"
                f"现由域 {domain} 覆盖（ToolDef 和 executor 均被替换）"
            )
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
        返回闭包，作为 LLMRouter.generate() 的 tool_executor 回调。

        所有 executor 签名统一: async (args: dict, ctx: ToolContext) -> str
        ctx 在运行时注入，executor 不持有任何外部 import。

        tool_calls 格式约定（由 LLMRouter 负责统一，无论模型厂商）:
        [{"id": str, "name": str, "arguments": str}]

        LLMRouter 内部将不同厂商的 tool call 响应标准化为以上格式后再调用
        tool_executor。ToolRegistry 不感知厂商差异。
        """
        names: Set[str] = set()
        for d in domains:
            names.update(self._domains.get(d, []))

        async def executor(tool_calls: List[Dict]) -> List[Dict]:
            results = []
            for tc in tool_calls:
                name = tc["name"]
                if name not in names or name not in self._executors:
                    logger.warning(
                        f"工具 {name} 不在请求 domain 或未注册，返回降级响应 "
                        f"(domains={list(names)[:5]}...)"
                    )
                    results.append({
                        "tool_call_id": tc["id"],
                        "content": f"工具 {name} 不可用",
                    })
                    continue
                raw_args = tc.get("arguments", "{}")
                try:
                    args = json.loads(raw_args)
                except json.JSONDecodeError as e:
                    logger.warning(
                        f"工具 {name} 参数解析失败: {e}, raw={str(raw_args)[:200]}"
                    )
                    results.append({
                        "tool_call_id": tc["id"],
                        "content": "参数解析失败，请重试或检查参数格式",
                    })
                    continue
                result = await self._executors[name](args, ctx)
                results.append({"tool_call_id": tc["id"], "content": str(result)})
            return results

        return executor
