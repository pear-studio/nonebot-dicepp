import warnings
warnings.warn(
    "memory.context_builder 已迁移到 chat.context，请更新 import（将在重构任务三完成后移除）",
    DeprecationWarning, stacklevel=2,
)

from ..chat.context import (  # noqa: F401
    ContextBuilder,
)
