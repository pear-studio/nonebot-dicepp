import warnings
warnings.warn(
    "agents.event_agent 已迁移到 life.event_agent，请更新 import（将在重构任务三完成后移除）",
    DeprecationWarning, stacklevel=2,
)

from ..life.event_agent import (  # noqa: F401
    EventGenerationAgent,
    EventGenerationResult,
    EventReactionResult,
    ShareMessageContext,
    EventContext,
)
