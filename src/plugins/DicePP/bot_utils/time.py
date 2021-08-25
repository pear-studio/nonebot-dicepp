import datetime

china_tz = datetime.timezone(datetime.timedelta(hours=8), '北京时间')
DATE_STR_FORMAT = '%Y_%m_%d_%H_%M_%S'
DATE_STR_FORMAT_DAY = '%Y_%m_%d'


def get_current_date_str() -> str:
    """
    返回以字符串表示的当前北京时间
    """
    current_date = datetime.datetime.now(china_tz)
    return current_date.strftime(DATE_STR_FORMAT)


def get_current_date_raw() -> datetime:
    """
    返回datetime格式的当前北京时间
    Returns:

    """
    current_date = datetime.datetime.now(china_tz)
    return current_date


def str_to_datetime(input_str: str) -> datetime:
    """
    将字符串表示的时间转换为datetime格式, 字符串格式由DATE_STR_FORMAT定义, 默认是%Y_%m_%d_%H_%M_%S
    """
    result = datetime.datetime.strptime(input_str, DATE_STR_FORMAT)
    result = result.replace(tzinfo=china_tz)
    return result


def datetime_to_str(input_datetime: datetime) -> str:
    """
    将datetime转换为字符串, 字符串格式由DATE_STR_FORMAT定义, 默认是%Y_%m_%d_%H_%M_%S
    """
    return input_datetime.strftime(DATE_STR_FORMAT)


def datetime_to_str_day(input_datetime: datetime) -> str:
    """
    将datetime转换为字符串, 字符串格式由DATE_STR_FORMAT_DAY定义, 默认是%Y_%m_%d
    """
    return input_datetime.strftime(DATE_STR_FORMAT_DAY)
