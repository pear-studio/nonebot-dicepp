import pytest
from utils.string import to_english_str, match_substring


@pytest.mark.unit
class TestToEnglishStr:
    def test_normal_ascii(self):
        result = to_english_str("hello world")
        assert result == "hello world"

    def test_chinese_punctuation(self):
        result = to_english_str("你好，世界")
        assert result == "你好,世界"

    def test_fullwidth_space(self):
        result = to_english_str("hello　world")
        assert result == "hello world"

    def test_fullwidth_punctuation(self):
        result = to_english_str("ｇｏｏｄ")
        assert result == "good"

    def test_mixed(self):
        result = to_english_str("ｇｏｏｄ！你好")
        assert result == "good!你好"

    def test_numbers(self):
        result = to_english_str("１２３")
        assert result == "123"

    def test_special_chars(self):
        result = to_english_str("（）")
        assert result == "()"

    def test_invalid_type(self):
        with pytest.raises(ValueError):
            to_english_str(123)

    def test_empty_string(self):
        result = to_english_str("")
        assert result == ""


@pytest.mark.unit
class TestMatchSubstring:
    def test_basic_match(self):
        result = match_substring("test", ["test1", "test2", "other"])
        assert result == ["test1", "test2"]

    def test_no_match(self):
        result = match_substring("xyz", ["abc", "def"])
        assert result == []

    def test_empty_list(self):
        result = match_substring("test", [])
        assert result == []

    def test_empty_substring(self):
        result = match_substring("", ["test", "abc"])
        assert result == ["test", "abc"]

    def test_case_sensitive(self):
        result = match_substring("Test", ["test1", "Test2", "TEST3"])
        assert result == ["Test2"]

    def test_partial_match(self):
        result = match_substring("ello", ["hello", "fellow", "jello"])
        assert result == ["hello", "fellow", "jello"]

    def test_with_chinese(self):
        result = match_substring("你好", ["你好世界", "再见", "你好的"])
        assert result == ["你好世界", "你好的"]

