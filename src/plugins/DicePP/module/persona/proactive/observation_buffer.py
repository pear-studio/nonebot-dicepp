import warnings
warnings.warn(
    "proactive.observation_buffer 已迁移到 life.observation，请更新 import（将在重构任务三完成后移除）",
    DeprecationWarning, stacklevel=2,
)

from ..life.observation import (  # noqa: F401
    BufferedMessage,
    DynamicThresholdConfig,
    ObservationBuffer,
)
