import warnings
warnings.warn(
    "proactive.scheduler 已迁移到 life.proactive，请更新 import（将在重构任务三完成后移除）",
    DeprecationWarning, stacklevel=2,
)

from ..life.proactive import (  # noqa: F401
    ProactiveConfig,
    ProactiveScheduler,
)

from ..wall_clock import persona_wall_now  # noqa: F401
