import pytest
from utils.data import yield_deduplicate


@pytest.mark.unit
class TestYieldDeduplicate:
    def test_empty_list(self):
        result = list(yield_deduplicate([]))
        assert result == []

    def test_no_duplicates(self):
        result = list(yield_deduplicate([1, 2, 3]))
        assert result == [1, 2, 3]

    def test_with_duplicates(self):
        result = list(yield_deduplicate([1, 2, 2, 3, 1, 4]))
        assert result == [1, 2, 3, 4]

    def test_with_key(self):
        result = list(yield_deduplicate([1, 2, 3, 4, 5], key=lambda x: x % 2))
        assert result == [1, 2]

    def test_with_strings(self):
        result = list(yield_deduplicate(["a", "b", "a", "c", "b"]))
        assert result == ["a", "b", "c"]

    def test_with_dicts(self):
        dicts = [{"id": 1}, {"id": 2}, {"id": 1}, {"id": 3}]
        result = list(yield_deduplicate(dicts, key=lambda x: x["id"]))
        assert len(result) == 3
        assert result[0]["id"] == 1
        assert result[1]["id"] == 2
        assert result[2]["id"] == 3

    def test_preserves_order(self):
        result = list(yield_deduplicate([3, 1, 4, 1, 5, 9, 2, 6]))
        assert result == [3, 1, 4, 5, 9, 2, 6]

