import unittest
import pytest
import datetime
from utils.time import (
    str_to_datetime, datetime_to_str, datetime_to_int, int_to_datetime,
    get_current_date_raw, datetime_to_str_day, datetime_to_str_week,
    datetime_to_str_month, datetime_filter_day, china_tz
)


@pytest.mark.unit
class TestStrToDatetime(unittest.TestCase):
    def test_standard_format(self):
        result = str_to_datetime("2024/01/15 10:30:45")
        self.assertIsInstance(result, datetime.datetime)
        self.assertEqual(result.year, 2024)
        self.assertEqual(result.month, 1)
        self.assertEqual(result.day, 15)

    def test_dash_format(self):
        result = str_to_datetime("2024-01-15 10:30:45")
        self.assertIsInstance(result, datetime.datetime)

    def test_underscore_format(self):
        result = str_to_datetime("2024_01_15 10:30:45")
        self.assertIsInstance(result, datetime.datetime)

    def test_invalid_format(self):
        with self.assertRaises(ValueError):
            str_to_datetime("invalid date")


@pytest.mark.unit
class TestDatetimeToStr(unittest.TestCase):
    def test_standard(self):
        dt = datetime.datetime(2024, 1, 15, 10, 30, 45, tzinfo=china_tz)
        result = datetime_to_str(dt)
        self.assertEqual(result, "2024/01/15 10:30:45")

    def test_with_timezone(self):
        dt = datetime.datetime(2024, 1, 15, 10, 30, 45, tzinfo=china_tz)
        result = datetime_to_str(dt)
        self.assertIn("2024", result)


@pytest.mark.unit
class TestDatetimeToInt(unittest.TestCase):
    def test_conversion(self):
        dt = datetime.datetime(2024, 1, 15, 10, 30, 45, tzinfo=china_tz)
        result = datetime_to_int(dt)
        self.assertIsInstance(result, int)
        self.assertGreater(result, 0)


@pytest.mark.unit
class TestIntToDatetime(unittest.TestCase):
    def test_conversion(self):
        timestamp = 1705299045
        result = int_to_datetime(timestamp)
        self.assertIsInstance(result, datetime.datetime)
        self.assertEqual(result.year, 2024)


@pytest.mark.unit
class TestGetCurrentDate(unittest.TestCase):
    def test_get_current_date_raw(self):
        result = get_current_date_raw()
        self.assertIsInstance(result, datetime.datetime)

    def test_timezone(self):
        result = get_current_date_raw()
        self.assertEqual(result.tzinfo, china_tz)


@pytest.mark.unit
class TestDatetimeToStrDay(unittest.TestCase):
    def test_conversion(self):
        dt = datetime.datetime(2024, 1, 15, 10, 30, 45, tzinfo=china_tz)
        result = datetime_to_str_day(dt)
        self.assertEqual(result, "2024_01_15")


@pytest.mark.unit
class TestDatetimeToStrWeek(unittest.TestCase):
    def test_conversion(self):
        dt = datetime.datetime(2024, 1, 15, 10, 30, 45, tzinfo=china_tz)
        result = datetime_to_str_week(dt)
        self.assertIn("2024", result)


@pytest.mark.unit
class TestDatetimeToStrMonth(unittest.TestCase):
    def test_conversion(self):
        dt = datetime.datetime(2024, 1, 15, 10, 30, 45, tzinfo=china_tz)
        result = datetime_to_str_month(dt)
        self.assertEqual(result, "2024_01")


@pytest.mark.unit
class TestDatetimeFilterDay(unittest.TestCase):
    def test_filter(self):
        dt = datetime.datetime(2024, 1, 15, 10, 30, 45, tzinfo=china_tz)
        result = datetime_filter_day(dt)
        self.assertEqual(result.hour, 0)
        self.assertEqual(result.minute, 0)
        self.assertEqual(result.second, 0)
        self.assertEqual(result.microsecond, 0)


if __name__ == '__main__':
    unittest.main()
