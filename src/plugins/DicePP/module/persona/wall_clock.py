"""Persona 模块统一「墙钟」时间（与 `PersonaConfig.timezone` 对齐的 naive 本地时间）。

约定：Persona 业务时间统一使用 `persona_wall_now()` 返回的 naive local datetime，
禁止在业务代码中直接使用 `datetime.now()` 或 `datetime.min`，以避免 naive/aware 混用风险。

本函数在时区无效或为空时会内部回退到 `datetime.now()`，业务代码无需额外处理。
"""

from __future__ import annotations

from typing import Optional, Union
from nonebot.log import logger
from datetime import datetime


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


def format_relative_time(
    ts: Optional[Union[datetime, str]],
    now: datetime,
) -> str:
    """返回相对时间描述，用于让 LLM 感知事件与当前时刻的时间距离。

    - ts: datetime / ISO str / None
    - now: 参考时间
    - 返回值: "刚刚" / "X分钟前" / "X小时前" / "X小时Y分钟前" / "X天前"
    - ts 为 None / 无效 / 未来时间 返回空字符串
    """
    if ts is None:
        return ""
    if isinstance(ts, str):
        try:
            ts = datetime.fromisoformat(ts)
        except ValueError:
            return ""
    if not isinstance(ts, datetime):
        return ""

    diff = now - ts
    seconds = diff.total_seconds()

    if seconds < 0:
        return ""
    if seconds < 60:
        return "刚刚"
    if seconds < 3600:
        return f"{int(seconds // 60)}分钟前"
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    if seconds < 86400:
        if minutes == 0:
            return f"{hours}小时前"
        return f"{hours}小时{minutes}分钟前"
    days = int(seconds // 86400)
    return f"{days}天前"


def format_timestamp(
    ts: Optional[Union[datetime, str]],
    now: datetime,
    fmt_today: str = "%H:%M",
    fmt_other: str = "%m-%d %H:%M",
) -> str:
    """格式化时间戳为可读字符串。

    - ts: datetime / ISO str / None
    - now: 参考时间（用于判断是否同日）
    - 返回值: 当日返回 HH:MM，隔日返回 MM-DD HH:MM，ts 为 None 返回空字符串
    """
    if ts is None:
        return ""
    if isinstance(ts, str):
        try:
            ts = datetime.fromisoformat(ts)
        except ValueError:
            return ""
    if not isinstance(ts, datetime):
        return ""
    return ts.strftime(fmt_today) if ts.date() == now.date() else ts.strftime(fmt_other)
