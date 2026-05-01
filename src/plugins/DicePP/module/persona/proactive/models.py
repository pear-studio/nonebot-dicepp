import warnings
warnings.warn(
    "proactive.models 已迁移到 life.models，请更新 import（将在重构任务三完成后移除）",
    DeprecationWarning, stacklevel=2,
)

from ..life.models import (  # noqa: F401
    ShareTarget,
)
