"""L2 对话链测试工具 — ScriptedProvider + FakeMessagePort + FakeImageGenProvider

替代外部依赖的假对象，mock 边界设在 provider.generate()。
"""

import json
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any

from plugins.DicePP.module.persona.llm.providers.protocol import (
    LLMResponse,
    LLMProvider,
    TokenUsage,
    ToolCall,
    ErrorClass,
)


# ── ScriptedProvider ──────────────────────────────────────────────────────────


@dataclass
class Step:
    """一次 generate() 调用的预设响应"""
    response: LLMResponse | Exception  # Exception → generate() 中抛出
    name: str = ""


class ScriptedProvider:
    """纯 seq 队列 LLMProvider，严格按序消费。

    作为文本客户端内部的可控模型响应，替代真实 LLM API 调用。
    耗尽后抛 RuntimeError（调用次数是确定性的，多调了就是 bug）。
    """

    retryable_errors: frozenset[str] = frozenset()

    def __init__(self, steps: List[Step]):
        self._steps = steps
        self._idx = 0
        self.calls: List[Dict[str, Any]] = []  # 记录每次 generate() 参数

    async def generate(
        self,
        messages: List[dict],
        tools: Optional[List[dict]] = None,
        temperature: Optional[float] = None,
        timeout: int = 60,
        tool_choice: Optional[str] = None,
        thinking: bool = False,
    ) -> LLMResponse:
        self.calls.append({
            "messages": messages,
            "tools": tools,
            "temperature": temperature,
            "tool_choice": tool_choice,
            "thinking": thinking,
        })
        if self._idx >= len(self._steps):
            raise RuntimeError(
                f"ScriptedProvider exhausted: {len(self._steps)} steps, "
                f"got call #{self._idx + 1}"
            )
        step = self._steps[self._idx]
        self._idx += 1
        if isinstance(step.response, Exception):
            raise step.response
        return step.response

    async def probe(self) -> bool:
        return True

    @staticmethod
    def classify_error(exception: Exception) -> ErrorClass:
        return ErrorClass.NON_RETRYABLE


# ── 快捷工厂 ─────────────────────────────────────────────────────────────────


def text(content: str, usage: Optional[TokenUsage] = None) -> Step:
    """创建纯文本 Step"""
    return Step(response=LLMResponse(
        content=content,
        usage=usage or TokenUsage(),
    ))


def tool(name: str, args: dict, usage: Optional[TokenUsage] = None) -> Step:
    """创建 tool_call Step（LLM 要求调用工具）"""
    return Step(response=LLMResponse(
        content=None,
        tool_calls=[ToolCall(id="c1", name=name, arguments=json.dumps(args))],
        usage=usage or TokenUsage(),
    ))


def error(exc: Exception) -> Step:
    """创建错误 Step（generate() 中抛出异常）"""
    return Step(response=exc)


# ── FakeMessagePort ───────────────────────────────────────────────────────────


class FakeMessagePort:
    """捕获发送消息，供测试断言分段发送。

    实现与 MessagePort.send() 兼容的接口。
    """

    def __init__(self):
        self.sent: List[Dict[str, Any]] = []

    async def send(
        self,
        user_id: str,
        group_id: str,
        content: str,
        **kwargs,
    ) -> bool:
        self.sent.append({
            "user_id": user_id,
            "group_id": group_id,
            "content": content,
            **kwargs,
        })
        return True


# ── FakeImageGenProvider ──────────────────────────────────────────────────────


class FakeImageGenProvider:
    """模拟图片生成，实现 ImageGenProvider 协议。

    作为独立图片能力测试 double。
    """

    max_prompt_chars: Optional[int] = 2000

    async def generate_image(self, prompt: str, **kwargs) -> str:
        return f"https://fake.image/{hash(prompt) & 0xFFFF}.png"

    async def probe(self) -> bool:
        return True

    @staticmethod
    def classify_error(exception: Exception) -> ErrorClass:
        return ErrorClass.NON_RETRYABLE
