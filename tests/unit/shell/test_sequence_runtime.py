"""Tests for shell/bot_runner.py utilities."""

import pytest

from plugins.DicePP.utils.sequence_runtime import SequenceRuntime


class TestSequenceRuntime:
    def test_roll_consumes_sequence(self):
        runtime = SequenceRuntime([20, 15, 8])
        assert runtime.roll(20) == 20
        assert runtime.roll(20) == 15
        assert runtime.roll(20) == 8
        assert runtime.get_consumed_count() == 3
        assert runtime.get_remaining_count() == 0

    def test_roll_normalizes_to_dice_range(self):
        runtime = SequenceRuntime([25, 0, -3])
        # 25 -> ((25-1) % 20) + 1 = 5
        assert runtime.roll(20) == 5
        # 0 -> ((-1) % 20) + 1 = 20
        assert runtime.roll(20) == 20
        # -3 -> ((-4) % 20) + 1 = 17
        assert runtime.roll(20) == 17

    def test_exhausted_raises_index_error(self):
        runtime = SequenceRuntime([1])
        runtime.roll(6)
        with pytest.raises(IndexError, match="exhausted"):
            runtime.roll(6)

    def test_invalid_dice_type_raises_assertion(self):
        runtime = SequenceRuntime([1])
        with pytest.raises(AssertionError, match="dice_type must be positive"):
            runtime.roll(0)

    def test_empty_sequence_immediately_exhausted(self):
        runtime = SequenceRuntime([])
        with pytest.raises(IndexError, match="exhausted"):
            runtime.roll(20)
