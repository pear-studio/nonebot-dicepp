"""
SequenceRuntime for deterministic dice rolling in tests.

Provides a runtime that returns values from a fixed sequence,
throwing IndexError when exhausted (rather than cycling).
"""

from typing import Sequence

from module.roll.karma_runtime import set_runtime, reset_runtime


class SequenceRuntime:
    """
    Deterministic runtime backed by a fixed sequence.

    Throws IndexError when the sequence is exhausted (rather than cycling),
    to expose "consumed more dice than expected" issues early.
    """

    def __init__(self, seq: Sequence[int]):
        self._seq = list(seq)
        self._idx = 0

    def roll(self, dice_type: int) -> int:
        """Consume one value from sequence and normalize to dice range.

        Args:
            dice_type: The dice type (e.g., 20 for d20). Must be > 0.

        Raises:
            AssertionError: If dice_type <= 0 (indicates programming error).
            IndexError: If sequence is exhausted.
        """
        assert dice_type > 0, f"dice_type must be positive, got {dice_type}"
        if self._idx >= len(self._seq):
            raise IndexError(
                f"SequenceRuntime exhausted: requested roll #{self._idx + 1} "
                f"but only {len(self._seq)} values available"
            )
        raw = self._seq[self._idx]
        self._idx += 1
        # Normalize into valid dice range [1, dice_type]
        return ((int(raw) - 1) % dice_type) + 1

    def get_consumed_count(self) -> int:
        """Return number of values consumed so far."""
        return self._idx

    def get_remaining_count(self) -> int:
        """Return number of values remaining in sequence."""
        return len(self._seq) - self._idx
