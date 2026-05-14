"""
LoopHook 协议与相关数据结构

定义 pre_llm / post_llm / post_tool / flush 四个 hook point
以及 PreLLMResult、LoopContext 数据类。
"""
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable, Optional, List, Dict, Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .providers.protocol import LLMProvider, LLMResponse


@dataclass
class ToolResult:
    """单个工具执行结果"""
    tool_call_id: str
    content: str


@dataclass
class RoundRecord:
    """单轮完整记录"""
    round: int
    think: Optional[str] = None
    tool_calls: Optional[List[dict]] = None
    tool_results: Optional[List[dict]] = None
    callback: Optional[dict] = None


@dataclass
class LoopContext:
    """AgentLoop 上下文，传递给所有 Hook 方法"""
    tool_round_num: int = 0
    callback_count: int = 0
    user_id: str = ""
    group_id: str = ""
    session_id: str = ""
    max_tool_rounds: int = 5
    max_round_callbacks: int = 3
    messages: Optional[List[dict]] = None
    provider: "Optional[LLMProvider]" = None
    mode: str = "chat"


@dataclass
class PreLLMResult:
    """pre_llm Hook 返回值"""
    messages: Optional[List[dict]] = None
    abort: bool = False
    abort_reason: str = ""


@runtime_checkable
class LoopHook(Protocol):
    """Agent 循环 Hook 协议

    所有方法均为可选实现。类属性 injects_message 区分观察型/注入型 Hook。
    """
    injects_message: bool = False

    async def pre_llm(self, messages: List[dict], ctx: LoopContext) -> PreLLMResult:
        ...

    async def post_llm(
        self, messages: List[dict], response: "LLMResponse", ctx: LoopContext
    ) -> Optional[dict]:
        ...

    async def post_tool(self, tool_results: List[ToolResult], ctx: LoopContext) -> None:
        ...

    async def flush(self, session_id: str, final_metadata: dict) -> None:
        ...
