"""SequenceRuntime for deterministic dice rolling in tests.

Re-exported from the production module to keep tests in sync.
"""

from plugins.DicePP.utils.sequence_runtime import (
    SequenceRuntime,
    set_runtime,
    reset_runtime,
)

__all__ = ["SequenceRuntime", "set_runtime", "reset_runtime"]
