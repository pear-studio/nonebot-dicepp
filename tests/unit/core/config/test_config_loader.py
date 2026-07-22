from core.config.loader import _deep_merge


def test_deep_merge_flat():
    result = _deep_merge({"a": 1, "b": 2}, {"b": 99, "c": 3})
    assert result == {"a": 1, "b": 99, "c": 3}


def test_deep_merge_nested():
    base = {"llm": {"enabled": False, "model": "old"}}
    override = {"llm": {"model": "new"}}
    result = _deep_merge(base, override)
    assert result["llm"]["enabled"] is False
    assert result["llm"]["model"] == "new"


def test_deep_merge_does_not_mutate_base():
    base = {"x": {"y": 1}}
    _deep_merge(base, {"x": {"z": 2}})
    assert "z" not in base["x"]
