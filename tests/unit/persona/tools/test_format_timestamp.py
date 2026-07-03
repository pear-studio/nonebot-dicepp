"""format_timestamp 单元测试"""
from datetime import datetime

from plugins.DicePP.utils.time import format_timestamp


class TestFormatTimestamp:
    """覆盖 format_timestamp 的 5 条分支路径"""

    def test_none_returns_empty(self):
        assert format_timestamp(None, datetime(2026, 5, 27, 12, 0, 0)) == ""

    def test_str_iso_today(self):
        now = datetime(2026, 5, 11, 14, 30)
        assert format_timestamp("2026-05-11T08:30:00", now) == "08:30"

    def test_str_iso_other_day(self):
        now = datetime(2026, 5, 11, 14, 30)
        assert format_timestamp("2026-05-09T14:00:00", now) == "05-09 14:00"

    def test_datetime_today(self):
        now = datetime(2026, 5, 11, 14, 30)
        assert format_timestamp(datetime(2026, 5, 11, 9, 15), now) == "09:15"

    def test_datetime_other_day(self):
        now = datetime(2026, 5, 11, 14, 30)
        assert format_timestamp(datetime(2026, 5, 9, 14, 0), now) == "05-09 14:00"

    def test_invalid_iso_string_returns_empty(self):
        """无效 ISO 字符串通过 str→ValueError 分支返回空字符串"""
        now = datetime(2026, 5, 11, 14, 30)
        assert format_timestamp("not a datetime", now) == ""

    def test_non_str_non_datetime_returns_empty(self):
        """int/float 等非 str 非 datetime 类型通过 'not isinstance(ts, datetime)' 分支返回空字符串"""
        now = datetime(2026, 5, 11, 14, 30)
        assert format_timestamp(42, now) == ""
        assert format_timestamp(3.14, now) == ""
