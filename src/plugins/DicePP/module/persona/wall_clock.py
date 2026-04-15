"""Persona 模块统一「墙钟」时间（与 `PersonaConfig.timezone` 对齐的 naive 本地时间）。

约定：Persona 业务时间统一使用 `persona_wall_now()` 返回的 naive local datetime，
禁止在业务代码中直接使用 `datetime.now()` 或 `datetime.min`，以避免 naive/aware 混用风险。

本函数在时区无效或为空时会内部回退到 `datetime.now()`，业务代码无需额外处理。
"""

from __future__ import annotations

import logging
from datetime import datetime

logger = logging.getLogger("persona.wall_clock")

PERSONA_EPOCH = datetime(2000, 1, 1)


def persona_wall_now(timezone_name: str) -> datetime:
    """
    返回配置时区下的当前本地时间，不带 tzinfo（与 SQLite / fromisoformat 存取一致）。

    若时区非法或 ZoneInfo 不可用，记录 warning 并回退到进程本地 `datetime.now()`。
    """
    if not timezone_name or not timezone_name.strip():
        return datetime.now()
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo(timezone_name.strip())).replace(tzinfo=None)
    except Exception as e:
        logger.warning(
            "persona_wall_now: 无效时区 %r，回退 naive now: %s",
            timezone_name,
            e,
        )
        return datetime.now()
