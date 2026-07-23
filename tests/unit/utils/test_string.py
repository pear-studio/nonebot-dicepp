import pytest
from plugins.DicePP.utils.string import to_english_str, match_substring, estimate_tokens


@pytest.mark.quick
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


class TestEstimateTokens:
    def test_empty_string(self):
        assert estimate_tokens("") == 0.0

    def test_pure_chinese(self):
        # 10 个中文字符 = 10 tokens
        result = estimate_tokens("你好世界你好世界你好")
        assert result == 10.0

    def test_pure_english(self):
        # 20 个英文字符 / 4 = 5 tokens
        result = estimate_tokens("hello world test str")
        assert result == 20 / 4

    def test_mixed_chinese_english(self):
        # "你好abc hello": 2 Chinese + 9 other = 2 + 9/4 = 4.25
        result = estimate_tokens("你好abc hello")
        assert result == 4.25

    def test_with_digits_and_symbols(self):
        # "测试123!@#": 2 Chinese + 6 other = 2 + 6/4 = 3.5
        result = estimate_tokens("测试123!@#")
        assert result == 3.5

    def test_only_whitespace(self):
        result = estimate_tokens("   ")
        assert result == 3 / 4

    def test_single_char(self):
        assert estimate_tokens("a") == 0.25
        assert estimate_tokens("中") == 1.0

