"""safe_json_loads 容错解析测试"""

import pytest

from plugins.DicePP.module.persona.utils.json_helpers import safe_json_loads


def test_direct_parse_success():
    assert safe_json_loads('{"a": 1}') == {"a": 1}
    assert safe_json_loads('[1, 2, 3]') == [1, 2, 3]


def test_strip_markdown_fence():
    text = "```json\n{\"a\": 1}\n```"
    assert safe_json_loads(text) == {"a": 1}


def test_bare_json_prefix():
    text = "json\n{\"a\": 1}"
    assert safe_json_loads(text) == {"a": 1}


def test_extract_balanced_with_escape():
    """字符串值中含 ``}`` 不应导致括号计数错位"""
    text = 'noise {"a": "abc}def"} trailing'
    assert safe_json_loads(text) == {"a": "abc}def"}


def test_extract_balanced_array():
    text = "prefix [1, 2, 3] suffix"
    assert safe_json_loads(text) == [1, 2, 3]


def test_fallback_on_invalid():
    assert safe_json_loads("not json at all", fallback=[]) == []
    assert safe_json_loads("not json", fallback=None) is None


def test_escape_sequence_in_string():
    """字符串中含转义引号不应导致 in_string 状态错乱"""
    text = '{"msg": "He said \\"hi\\""}'
    assert safe_json_loads(text) == {"msg": 'He said "hi"'}
