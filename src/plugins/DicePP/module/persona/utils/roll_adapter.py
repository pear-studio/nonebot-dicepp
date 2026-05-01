import warnings
warnings.warn(
    "utils.roll_adapter 已迁移到 tools.roll_dice，请更新 import（将在重构任务三完成后移除）",
    DeprecationWarning, stacklevel=2,
)

from ..tools.roll_dice import (  # noqa: F401
    RollAdapter,
)
