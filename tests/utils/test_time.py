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
        assert isinstance(result, datetime.datetime)
        assert result.year == 2024
        assert result.month == 1
        assert result.day == 15

    def test_dash_format(self):
        result = str_to_datetime("2024-01-15 10:30:45")
        assert isinstance(result, datetime.datetime)

    def test_underscore_format(self):
        result = str_to_datetime("2024_01_15 10:30:45")
        assert isinstance(result, datetime.datetime)

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
        assert "2024" in result


@pytest.mark.unit
class TestDatetimeToInt:
    def test_conversion(self):
        dt = datetime.datetime(2024, 1, 15, 10, 30, 45, tzinfo=china_tz)
        result = datetime_to_int(dt)
        assert isinstance(result, int)
        assert result > 0


@pytest.mark.unit
class TestIntToDatetime:
    def test_conversion(self):
        timestamp = 1705299045
        result = int_to_datetime(timestamp)
        assert isinstance(result, datetime.datetime)
        assert result.year == 2024


@pytest.mark.unit
class TestGetCurrentDate:
    def test_get_current_date_raw(self):
        result = get_current_date_raw()
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
        assert "2024" in result


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

