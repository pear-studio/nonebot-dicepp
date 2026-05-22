"""
LLMProvider 协议与标准化数据结构
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol, runtime_checkable, List, Optional


class ErrorClass(str, Enum):
    NON_RETRYABLE = "non_retryable"
    RETRYABLE = "retryable"


class NonRetryableError(Exception):
    """不可重试的 LLM 错误（认证失败、内容过滤等）"""
    pass


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

    async def probe(self) -> bool:
        """Health check: 发送 max_tokens=1 的 completion 请求。成功返回 True。"""
        ...

    @staticmethod
    def classify_error(exception: Exception) -> ErrorClass:
        """将异常分类为 NON_RETRYABLE 或 RETRYABLE。"""
        ...


@runtime_checkable
class ImageGenProvider(Protocol):
    """图片生成供应商协议"""

    max_prompt_chars: Optional[int] = None

    async def generate_image(self, prompt: str, **kwargs) -> str:
        """生成图片，返回 URL。"""
        ...

    async def probe(self) -> bool:
        """Health check: 轻量 API 调用验证可用性。成功返回 True。"""
        ...

    @staticmethod
    def classify_error(exception: Exception) -> ErrorClass:
        """将异常分类为 NON_RETRYABLE 或 RETRYABLE。"""
        ...
