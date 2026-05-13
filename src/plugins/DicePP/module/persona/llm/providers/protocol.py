"""
LLMProvider 协议与标准化数据结构
"""
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable, List, Optional


@dataclass
class ToolCall:
    """标准化工具调用"""
    id: str
    name: str
    arguments: str

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "arguments": self.arguments}


@dataclass
class TokenUsage:
    input: int = 0
    output: int = 0
    cached: int = 0


@dataclass
class LLMResponse:
    content: Optional[str]
    tool_calls: List[ToolCall] = field(default_factory=list)
    usage: TokenUsage = field(default_factory=TokenUsage)
    finish_reason: str = "stop"
    model: str = ""


@runtime_checkable
class LLMProvider(Protocol):
    """LLM 供应商协议"""

    retryable_errors: frozenset[str]

    async def generate(
        self,
        messages: List[dict],
        tools: Optional[List[dict]] = None,
        temperature: Optional[float] = None,
        timeout: int = 60,
    ) -> LLMResponse:
        ...
