import warnings
warnings.warn(
    "agents.scoring_agent 已迁移到 chat.scoring，请更新 import（将在重构任务三完成后移除）",
    DeprecationWarning, stacklevel=2,
)

from ..chat.scoring import (  # noqa: F401
    ScoringAgent,
)
