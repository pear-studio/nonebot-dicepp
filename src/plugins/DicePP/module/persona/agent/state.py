"""AgentRunState — 单次 Agent run 的运行时状态"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class AgentRunState:
    """单次 Agent run 的可变状态，由 AgentLoop 维护

    设计原则：
    - 所有可变状态集中在这里，不散落在 loop 局部变量中。
    - AgentEventBus 和 sinks 可以读取但不能直接修改（sink_failures 例外）。
    - 第一版不实现 replay/resume，state 只存活于内存。
    """

    run_id: str
    turn_id: str
    user_id: str
    group_id: str
    mode: str  # "segmented_chat" | "structured_collect" | "proactive" ...

    status: str = "running"
    messages: List[dict] = field(default_factory=list)

    tool_rounds: int = 0
    correction_count: int = 0
    warning_count: int = 0
    interim_segment_count: int = 0

    sink_failures: List[str] = field(default_factory=list)

    final_text: str = ""
    delivery_performed: bool = False
    final_reason: str = ""
    error: str = ""

    # Phase 3: 图片理解 — observation 方案
    pending_images: Optional[Dict[str, str]] = None
