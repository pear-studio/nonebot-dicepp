import time
import datetime

from typing import Optional, Union
from nonebot.log import logger

china_tz = datetime.timezone(datetime.timedelta(hours=8), "北京时间")
DATE_STR_FORMAT = "%Y/%m/%d %H:%M:%S"
DATE_STR_FORMAT_DAY = "%Y_%m_%d"
DATE_STR_FORMAT_WEEK = "%Y_%W"
DATE_STR_FORMAT_MONTH = "%Y_%m"

DEFAULT_EPOCH = datetime.datetime(2000, 1, 1)

# 兼容历史数据中使用下划线或短横线分隔的时间格式
_DATE_STR_COMPAT_FORMATS = [
    DATE_STR_FORMAT,
    "%Y-%m-%d %H:%M:%S",
    "%Y_%m_%d %H:%M:%S",
    "%Y-%m-%d_%H_%M_%S",
    "%Y_%m_%d_%H_%M_%S",
]


def wall_now(timezone_name: str = "Asia/Shanghai") -> datetime.datetime:
    """
    返回配置时区下的当前本地时间，不带 tzinfo（与 SQLite / fromisoformat 存取一致）。

    若时区非法或 ZoneInfo 不可用，记录 warning 并回退到进程本地 `datetime.now()`。
    """
    if not timezone_name or not timezone_name.strip():
        return datetime.datetime.now()
    try:
        from zoneinfo import ZoneInfo

        return datetime.datetime.now(ZoneInfo(timezone_name.strip())).replace(tzinfo=None)
    except Exception as e:
        logger.warning(
            "wall_now: 无效时区 %r，回退 naive now: %s",
            timezone_name,
            e,
        )
        return datetime.datetime.now()


def format_relative_time(
    ts: Optional[Union[datetime.datetime, str]],
    now: datetime.datetime,
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
            ts = datetime.datetime.fromisoformat(ts)
        except ValueError:
            return ""
    if not isinstance(ts, datetime.datetime):
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
    ts: Optional[Union[datetime.datetime, str]],
    now: datetime.datetime,
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
            ts = datetime.datetime.fromisoformat(ts)
        except ValueError:
            return ""
    if not isinstance(ts, datetime.datetime):
        return ""
    return ts.strftime(fmt_today) if ts.date() == now.date() else ts.strftime(fmt_other)


def str_to_datetime(input_str: str) -> datetime:
    """
    将字符串表示的时间转换为datetime格式, 支持多种历史格式兼容
    """
    for fmt in _DATE_STR_COMPAT_FORMATS:
        try:
            result = datetime.datetime.strptime(input_str, fmt)
            return result.replace(tzinfo=china_tz)
        except ValueError:
            continue
    raise ValueError(f"无法解析的时间格式: {input_str}")


def datetime_to_str(input_datetime: datetime) -> str:
    """
    将datetime转换为字符串, 字符串格式由DATE_STR_FORMAT定义, 默认是%Y/%m/%d %H:%M:%S
    """
    return input_datetime.strftime(DATE_STR_FORMAT)


def datetime_to_int(input_datetime: datetime) -> int:
    """
    将datetime转换为int, 即localtime, 时区默认为东八区, 单位为秒
    """
    return int(time.mktime(input_datetime.timetuple()))


def int_to_datetime(timestamp: int) -> datetime:
    """
    将int转换为datetime, 时区默认为东八区, 单位为秒
    """
    return datetime.datetime.fromtimestamp(timestamp, tz=china_tz)


def get_current_date_raw() -> datetime.datetime:
    """
    返回datetime格式的当前北京时间（带 tzinfo）
    """
    return datetime.datetime.now(china_tz)


def get_current_date_str() -> str:
    """
    返回以字符串表示的当前北京时间
    """
    return datetime_to_str(get_current_date_raw())


def get_current_date_int() -> int:
    """
    返回int格式的当前北京时间
    """
    return datetime_to_int(get_current_date_raw())


def datetime_to_str_day(input_datetime: datetime) -> str:
    """
    将datetime转换为字符串, 字符串格式由DATE_STR_FORMAT_DAY定义, 默认是%Y_%m_%d
    """
    return input_datetime.strftime(DATE_STR_FORMAT_DAY)


def datetime_filter_day(input_datetime: datetime.datetime) -> datetime:
    """
    只保留datetime的date部分
    """
    return input_datetime.replace(hour=0, minute=0, second=0, microsecond=0)
    
def datetime_to_str_week(input_datetime: datetime) -> str:
    """
    将datetime转换为字符串, 字符串格式由DATE_STR_FORMAT_WEEK定义, 默认是%Y_%W
    """
    return input_datetime.strftime(DATE_STR_FORMAT_WEEK)

def datetime_to_str_month(input_datetime: datetime) -> str:
    """
    将datetime转换为字符串, 字符串格式由DATE_STR_FORMAT_MONTH定义, 默认是%Y_%m
    """
    return input_datetime.strftime(DATE_STR_FORMAT_MONTH)
