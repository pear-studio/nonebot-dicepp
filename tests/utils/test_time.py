import pytest
import datetime
from utils.time import (
    str_to_datetime, datetime_to_str, datetime_to_int, int_to_datetime,
    get_current_date_raw, datetime_to_str_day, datetime_to_str_week,
    datetime_to_str_month, datetime_filter_day, china_tz
)


@pytest.mark.unit
class TestStrToDatetime:
    def test_standard_format(self):
        result = str_to_datetime("2024/01/15 10:30:45")
        assert result == datetime.datetime(2024, 1, 15, 10, 30, 45, tzinfo=china_tz)

    def test_dash_format(self):
        result = str_to_datetime("2024-01-15 10:30:45")
        assert result == datetime.datetime(2024, 1, 15, 10, 30, 45, tzinfo=china_tz)

    def test_underscore_format(self):
        result = str_to_datetime("2024_01_15 10:30:45")
        assert result == datetime.datetime(2024, 1, 15, 10, 30, 45, tzinfo=china_tz)

    def test_invalid_format(self):
        with pytest.raises(ValueError):
            str_to_datetime("invalid date")


@pytest.mark.unit
class TestDatetimeToStr:
    def test_standard(self):
        dt = datetime.datetime(2024, 1, 15, 10, 30, 45, tzinfo=china_tz)
        result = datetime_to_str(dt)
        assert result == "2024/01/15 10:30:45"

    def test_with_timezone(self):
        dt = datetime.datetime(2024, 1, 15, 10, 30, 45, tzinfo=china_tz)
        result = datetime_to_str(dt)
        assert result == "2024/01/15 10:30:45"


@pytest.mark.unit
class TestDatetimeToInt:
    def test_conversion(self):
        # 测试环境本地时区为东八区，与 china_tz 一致。
        # datetime_to_int 通过 time.mktime(timetuple()) 将 datetime 视为本地时间戳。
        # 2024-01-15 14:10:45 +0800 == 1705299045
        dt = datetime.datetime(2024, 1, 15, 14, 10, 45, tzinfo=china_tz)
        result = datetime_to_int(dt)
        assert isinstance(result, int)
        assert result == 1705299045


@pytest.mark.unit
class TestIntToDatetime:
    def test_conversion(self):
        timestamp = 1705299045
        result = int_to_datetime(timestamp)
        assert result == datetime.datetime(2024, 1, 15, 14, 10, 45, tzinfo=china_tz)


@pytest.mark.unit
class TestGetCurrentDate:
    def test_get_current_date_raw(self):
        result = get_current_date_raw()
        # 精确值依赖当前运行时间，无法固定预期值
        assert isinstance(result, datetime.datetime)

    def test_timezone(self):
        result = get_current_date_raw()
        assert result.tzinfo == china_tz


@pytest.mark.unit
class TestDatetimeToStrDay:
    def test_conversion(self):
        dt = datetime.datetime(2024, 1, 15, 10, 30, 45, tzinfo=china_tz)
        result = datetime_to_str_day(dt)
        assert result == "2024_01_15"


@pytest.mark.unit
class TestDatetimeToStrWeek:
    def test_conversion(self):
        dt = datetime.datetime(2024, 1, 15, 10, 30, 45, tzinfo=china_tz)
        result = datetime_to_str_week(dt)
        assert result == "2024_03"


@pytest.mark.unit
class TestDatetimeToStrMonth:
    def test_conversion(self):
        dt = datetime.datetime(2024, 1, 15, 10, 30, 45, tzinfo=china_tz)
        result = datetime_to_str_month(dt)
        assert result == "2024_01"


@pytest.mark.unit
class TestDatetimeFilterDay:
    def test_filter(self):
        dt = datetime.datetime(2024, 1, 15, 10, 30, 45, tzinfo=china_tz)
        result = datetime_filter_day(dt)
        assert result.hour == 0
        assert result.minute == 0
        assert result.second == 0
        assert result.microsecond == 0

