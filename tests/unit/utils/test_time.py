import time as time_module
import pytest
import datetime
from utils.time import (
    str_to_datetime, datetime_to_str, datetime_to_int, int_to_datetime,
    get_current_date_raw, datetime_to_str_day, datetime_to_str_week,
    datetime_to_str_month, datetime_filter_day, china_tz,
    format_relative_time, wall_now,
)


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


class TestDatetimeToStr:
    def test_standard(self):
        dt = datetime.datetime(2024, 1, 15, 10, 30, 45, tzinfo=china_tz)
        result = datetime_to_str(dt)
        assert result == "2024/01/15 10:30:45"


class TestDatetimeToInt:
    def test_aware_datetime_uses_its_own_timezone(self, monkeypatch):
        monkeypatch.setattr(time_module, "mktime", lambda _: 0)
        dt = datetime.datetime(2024, 1, 15, 14, 10, 45, tzinfo=china_tz)
        same_in_utc = datetime.datetime(
            2024, 1, 15, 6, 10, 45, tzinfo=datetime.timezone.utc
        )

        assert datetime_to_int(dt) == 1705299045
        assert datetime_to_int(same_in_utc) == 1705299045

    def test_naive_datetime_is_interpreted_as_beijing_time(self, monkeypatch):
        monkeypatch.setattr(time_module, "mktime", lambda _: 0)
        dt = datetime.datetime(2024, 1, 15, 14, 10, 45)

        assert datetime_to_int(dt) == 1705299045


class TestIntToDatetime:
    def test_conversion(self):
        timestamp = 1705299045
        result = int_to_datetime(timestamp)
        assert result == datetime.datetime(2024, 1, 15, 14, 10, 45, tzinfo=china_tz)


class TestGetCurrentDate:
    def test_get_current_date_raw(self):
        result = get_current_date_raw()
        # 精确值依赖当前运行时间，无法固定预期值
        assert isinstance(result, datetime.datetime)

    def test_timezone(self):
        result = get_current_date_raw()
        assert result.tzinfo == china_tz


class TestDatetimeToStrDay:
    def test_conversion(self):
        dt = datetime.datetime(2024, 1, 15, 10, 30, 45, tzinfo=china_tz)
        result = datetime_to_str_day(dt)
        assert result == "2024_01_15"


class TestDatetimeToStrWeek:
    def test_conversion(self):
        dt = datetime.datetime(2024, 1, 15, 10, 30, 45, tzinfo=china_tz)
        result = datetime_to_str_week(dt)
        assert result == "2024_03"


class TestDatetimeToStrMonth:
    def test_conversion(self):
        dt = datetime.datetime(2024, 1, 15, 10, 30, 45, tzinfo=china_tz)
        result = datetime_to_str_month(dt)
        assert result == "2024_01"


class TestDatetimeFilterDay:
    def test_filter(self):
        dt = datetime.datetime(2024, 1, 15, 10, 30, 45, tzinfo=china_tz)
        result = datetime_filter_day(dt)
        assert result.hour == 0
        assert result.minute == 0
        assert result.second == 0
        assert result.microsecond == 0


class TestFormatRelativeTime:
    """format_relative_time 边界测试"""

    def test_none_returns_empty(self):
        now = datetime.datetime(2024, 6, 1, 12, 0, 0)
        assert format_relative_time(None, now) == ""

    def test_invalid_string_returns_empty(self):
        now = datetime.datetime(2024, 6, 1, 12, 0, 0)
        assert format_relative_time("not a date", now) == ""

    def test_non_datetime_returns_empty(self):
        now = datetime.datetime(2024, 6, 1, 12, 0, 0)
        assert format_relative_time(12345, now) == ""

    def test_future_time_returns_empty(self):
        now = datetime.datetime(2024, 6, 1, 12, 0, 0)
        future = now + datetime.timedelta(hours=1)
        assert format_relative_time(future, now) == ""

    def test_just_now(self):
        now = datetime.datetime(2024, 6, 1, 12, 0, 0)
        ts = now - datetime.timedelta(seconds=30)
        assert format_relative_time(ts, now) == "刚刚"

    def test_minutes_ago(self):
        now = datetime.datetime(2024, 6, 1, 12, 0, 0)
        ts = now - datetime.timedelta(minutes=5)
        assert format_relative_time(ts, now) == "5分钟前"

    def test_hours_ago(self):
        now = datetime.datetime(2024, 6, 1, 12, 0, 0)
        ts = now - datetime.timedelta(hours=3)
        assert format_relative_time(ts, now) == "3小时前"

    def test_hours_and_minutes_ago(self):
        now = datetime.datetime(2024, 6, 1, 12, 0, 0)
        ts = now - datetime.timedelta(hours=3, minutes=20)
        assert format_relative_time(ts, now) == "3小时20分钟前"

    def test_days_ago(self):
        now = datetime.datetime(2024, 6, 1, 12, 0, 0)
        ts = now - datetime.timedelta(days=3)
        assert format_relative_time(ts, now) == "3天前"

    def test_iso_string_input(self):
        now = datetime.datetime(2024, 6, 1, 12, 0, 0)
        ts_str = "2024-06-01T11:55:00"
        assert format_relative_time(ts_str, now) == "5分钟前"

    def test_boundary_60_seconds(self):
        now = datetime.datetime(2024, 6, 1, 12, 0, 0)
        # 59 seconds → "刚刚"
        ts = now - datetime.timedelta(seconds=59)
        assert format_relative_time(ts, now) == "刚刚"
        # 60 seconds → "1分钟前"
        ts = now - datetime.timedelta(seconds=60)
        assert format_relative_time(ts, now) == "1分钟前"

    def test_boundary_60_minutes(self):
        now = datetime.datetime(2024, 6, 1, 12, 0, 0)
        # 59 minutes → "59分钟前"
        ts = now - datetime.timedelta(minutes=59)
        assert format_relative_time(ts, now) == "59分钟前"
        # 60 minutes → "1小时前"
        ts = now - datetime.timedelta(minutes=60)
        assert format_relative_time(ts, now) == "1小时前"

    def test_boundary_24_hours(self):
        now = datetime.datetime(2024, 6, 1, 12, 0, 0)
        # 23 hours → "23小时前"
        ts = now - datetime.timedelta(hours=23)
        assert format_relative_time(ts, now) == "23小时前"
        # 24 hours → "1天前"
        ts = now - datetime.timedelta(hours=24)
        assert format_relative_time(ts, now) == "1天前"


class TestWallNow:
    """wall_now 时区回退行为测试"""

    def test_empty_timezone(self):
        result = wall_now("")
        assert result is not None
        assert isinstance(result, datetime.datetime)

    def test_none_timezone(self):
        result = wall_now(None)
        assert result is not None
        assert isinstance(result, datetime.datetime)

    def test_whitespace_timezone(self):
        result = wall_now("   ")
        assert result is not None
        assert isinstance(result, datetime.datetime)

    def test_invalid_timezone(self):
        result = wall_now("Mars/City")
        assert result is not None
        assert isinstance(result, datetime.datetime)

    def test_valid_timezone(self):
        result = wall_now("Asia/Shanghai")
        assert result is not None
        assert isinstance(result, datetime.datetime)

    def test_valid_timezone_utc(self):
        result = wall_now("UTC")
        assert result is not None
        assert isinstance(result, datetime.datetime)

