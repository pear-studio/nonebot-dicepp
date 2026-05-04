import warnings
warnings.warn(
    "proactive.character_life 已迁移到 life.character_life，请更新 import",
    DeprecationWarning, stacklevel=2,
)

from ..life.character_life import (  # noqa: F401
    CharacterLife,
    CharacterLifeConfig,
    OngoingActivity,
)
