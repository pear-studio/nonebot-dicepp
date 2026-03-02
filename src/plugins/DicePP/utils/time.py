import time
import datetime

china_tz = datetime.timezone(datetime.timedelta(hours=8), "北京时间")
DATE_STR_FORMAT = "%Y/%m/%d %H:%M:%S"
DATE_STR_FORMAT_DAY = "%Y_%m_%d"
DATE_STR_FORMAT_WEEK = "%Y_%W"
DATE_STR_FORMAT_MONTH = "%Y_%m"

# 兼容历史数据中使用下划线或短横线分隔的时间格式
_DATE_STR_COMPAT_FORMATS = [
    DATE_STR_FORMAT,
    "%Y-%m-%d %H:%M:%S",
    "%Y_%m_%d %H:%M:%S",
    "%Y-%m-%d_%H_%M_%S",
    "%Y_%m_%d_%H_%M_%S",
]


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


def get_current_date_raw() -> datetime:
    """
    返回datetime格式的当前北京时间
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
