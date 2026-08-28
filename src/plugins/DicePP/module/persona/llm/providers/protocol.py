"""LLM 调用的标准化数据结构与图片生成协议。"""
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable, List, Optional


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
    input: int = 0          # 未命中缓存的输入 token
    output: int = 0         # 输出 token（不含推理 token，与 reasoning 互斥）
    cache_read: int = 0     # 缓存命中读取的 token（替代原 cached）
    cache_creation: int = 0 # 缓存写入的 token
    reasoning: int = 0      # 推理 token
    # 注意：output 与 reasoning 互斥。
    # DeepSeek 的 completion_tokens 包含 reasoning_tokens，需做减法；
    # OpenAI 的 completion_tokens 只算非推理 tokens，直接赋值。
    # 因此 output = 纯文本输出，reasoning = 推理 tokens，两者互斥。
    usage_status: str = ""      # "ok" / "missing" / "malformed"
    usage_raw_json: str = ""    # 原始 usage JSON
    usage_note: str = ""        # 异常说明


@dataclass
class LLMResponse:
    content: Optional[str]           # API 返回的文本。契约：provider 层保证 content 不含 <think> 标签
    tool_calls: List[ToolCall] = field(default_factory=list)
    usage: TokenUsage = field(default_factory=TokenUsage)
    finish_reason: str = "stop"
    model: str = ""
    reasoning_content: Optional[str] = None  # 思考链文本
    latency_ms: Optional[float] = None       # 调用耗时（毫秒）


@runtime_checkable
class ImageGenProvider(Protocol):
    """图片生成供应商协议"""

    max_prompt_chars: Optional[int] = None

    async def generate_image(self, prompt: str, **kwargs) -> str:
        """生成图片，返回 URL。"""
        ...
