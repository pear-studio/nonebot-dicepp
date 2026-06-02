"""SelectionPolicy — 能力驱动的模型选择策略常量。

SCORING / EVENT_GEN / DIARY / SUMMARIZE 四个常量当前使用相同配置
（llm + text/tool_calls + cost 优先），各自保留独立常量而非合并为单一别名，
是为了未来差异化需求预留扩展点——例如 SCORING 可能改为 prefer_quality、
DIARY 可能需要降低 capabilities 要求等。
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class SelectionPolicy:
    category: str
    required_capabilities: tuple[str, ...]
    prefer_quality: bool
    prefer_cost: bool

    def __post_init__(self):
        if self.prefer_quality and self.prefer_cost:
            raise ValueError("prefer_quality 和 prefer_cost 不能同时为 True")
        if not self.prefer_quality and not self.prefer_cost:
            raise ValueError("prefer_quality 和 prefer_cost 必须至少一个为 True")


CHAT = SelectionPolicy(
    category="llm", required_capabilities=("text", "tool_calls"),
    prefer_quality=True, prefer_cost=False,
)
SCORING = SelectionPolicy(
    category="llm", required_capabilities=("text", "tool_calls"),
    prefer_quality=False, prefer_cost=True,
)
EVENT_GEN = SelectionPolicy(
    category="llm", required_capabilities=("text", "tool_calls"),
    prefer_quality=False, prefer_cost=True,
)
DIARY = SelectionPolicy(
    category="llm", required_capabilities=("text", "tool_calls"),
    prefer_quality=False, prefer_cost=True,
)
SUMMARIZE = SelectionPolicy(
    category="llm", required_capabilities=("text", "tool_calls"),
    prefer_quality=False, prefer_cost=True,
)
CHAT_WITH_IMAGE = SelectionPolicy(
    category="llm", required_capabilities=("text", "tool_calls", "image_input"),
    prefer_quality=True, prefer_cost=False,
)
