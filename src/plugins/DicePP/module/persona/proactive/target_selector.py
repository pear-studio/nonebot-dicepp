import warnings
warnings.warn(
    "proactive.target_selector 已迁移到 life.target，请更新 import（将在重构任务三完成后移除）",
    DeprecationWarning, stacklevel=2,
)

from ..life.target import (  # noqa: F401
    TargetSelector,
    FORCE_PRIORITY_BASE,
    NORMAL_HIGH_PRIORITY_BASE,
    NORMAL_MEDIUM_PRIORITY_BASE,
    FORCE_LIST_WARNING_THRESHOLD,
)
