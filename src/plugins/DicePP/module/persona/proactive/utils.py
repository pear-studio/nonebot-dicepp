import warnings
warnings.warn(
    "proactive.utils 已迁移到 life.utils，请更新 import（将在重构任务三完成后移除）",
    DeprecationWarning, stacklevel=2,
)

from ..life.utils import (  # noqa: F401
    effective_for_proactive,
)
