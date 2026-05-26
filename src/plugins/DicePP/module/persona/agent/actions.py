"""Action 定义 — ToolExecutor 返回值，由特定 Sink 消费

EXTERNAL_ACTION 工具不直接在 executor 中完成副作用，
而是返回 DeclaredAction，由对应的 Sink 异步执行。
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional


class EffectKind(str, Enum):
    """工具副作用分类"""

    PURE = "pure"
    STATE_WRITE = "state_write"
    EXTERNAL_ACTION = "external_action"


@dataclass
class SendMessageAction:
    """send_reply_segment 的声明式 action，由 DeliverySink 消费"""

    content: str
    phase: str = "final"  # "interim" | "final"
    delay_before: float = 1.0
    segment_index: int = 0
    action_id: str = ""


@dataclass
class GenerateImageAction:
    """generate_image 的声明式 action，由 ImageGenerationSink 消费"""

    prompt: str
    action_id: str = ""


@dataclass
class DeclaredAction:
    """EXTERNAL_ACTION 工具的通用返回值"""

    action_id: str
    action_type: str  # "send_message" | "generate_image"
    payload: Dict[str, Any]
