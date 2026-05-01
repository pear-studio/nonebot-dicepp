import warnings
warnings.warn(
    "utils.privacy 已迁移到 llm.privacy，请更新 import（将在重构任务三完成后移除）",
    DeprecationWarning, stacklevel=2,
)

from ..llm.privacy import (  # noqa: F401
    mask_sensitive_string,
)
