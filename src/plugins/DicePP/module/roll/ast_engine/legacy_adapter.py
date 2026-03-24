"""
Legacy Engine Adapter — Isolated Module Boundary
================================================

This module is the **single removable seam** between the AST engine and the
legacy regex-based roll engine.  All cross-engine calls from ``adapter.py``
flow through here so that:

1. The legacy implementation can be deleted by removing this file plus the
   corresponding functions in ``expression.py``, without touching any other
   ``ast_engine`` code.
2. The call graph stays explicit: nothing inside ``ast_engine/`` should import
   from ``..expression`` or ``..roll_utils`` directly — use this module instead.

Scope boundary (per design decision 12):
- This change ONLY isolates legacy behind this boundary.
- The actual deletion of legacy code is deferred to a later independent change.
"""

from ..roll_utils import RollDiceError


def call_legacy_engine(expression: str):
    """
    Execute ``expression`` through the legacy roll engine and return a
    ``RollResult``.

    This is the *only* entry point from ``ast_engine`` into legacy code.
    Replace or stub this function to cut the legacy dependency entirely.

    Args:
        expression: Raw (already-preprocessed or raw) expression string.

    Returns:
        ``RollResult`` as produced by the legacy engine.

    Raises:
        ``RollDiceError``: propagated unchanged so callers can decide whether
        to wrap it into an AST engine error type.
    """
    from ..expression import exec_roll_exp_legacy  # deferred to avoid circular import at module load

    return exec_roll_exp_legacy(expression)


__all__ = ["call_legacy_engine", "RollDiceError"]
