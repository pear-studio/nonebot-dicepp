import unittest
import pytest
from utils.string import to_english_str, match_substring


@pytest.mark.unit
class TestToEnglishStr(unittest.TestCase):
    def test_normal_ascii(self):
        result = to_english_str("hello world")
        self.assertEqual(result, "hello world")

    def test_chinese_punctuation(self):
        result = to_english_str("你好，世界")
        self.assertEqual(result, "你好,世界")

    def test_fullwidth_space(self):
        result = to_english_str("hello　world")
        self.assertEqual(result, "hello world")

    def test_fullwidth_punctuation(self):
        result = to_english_str("ｇｏｏｄ")
        self.assertEqual(result, "good")

    def test_mixed(self):
        result = to_english_str("ｇｏｏｄ！你好")
        self.assertEqual(result, "good!你好")

    def test_numbers(self):
        result = to_english_str("１２３")
        self.assertEqual(result, "123")

    def test_special_chars(self):
        result = to_english_str("（）")
        self.assertEqual(result, "()")

    def test_invalid_type(self):
        with self.assertRaises(ValueError):
            to_english_str(123)

    def test_empty_string(self):
        result = to_english_str("")
        self.assertEqual(result, "")


@pytest.mark.unit
class TestMatchSubstring(unittest.TestCase):
    def test_basic_match(self):
        result = match_substring("test", ["test1", "test2", "other"])
        self.assertEqual(result, ["test1", "test2"])

    def test_no_match(self):
        result = match_substring("xyz", ["abc", "def"])
        self.assertEqual(result, [])

    def test_empty_list(self):
        result = match_substring("test", [])
        self.assertEqual(result, [])

    def test_empty_substring(self):
        result = match_substring("", ["test", "abc"])
        self.assertEqual(result, ["test", "abc"])

    def test_case_sensitive(self):
        result = match_substring("Test", ["test1", "Test2", "TEST3"])
        self.assertEqual(result, ["Test2"])

    def test_partial_match(self):
        result = match_substring("ello", ["hello", "fellow", "jello"])
        self.assertEqual(result, ["hello", "fellow", "jello"])

    def test_with_chinese(self):
        result = match_substring("你好", ["你好世界", "再见", "你好的"])
        self.assertEqual(result, ["你好世界", "你好的"])


if __name__ == '__main__':
    unittest.main()
