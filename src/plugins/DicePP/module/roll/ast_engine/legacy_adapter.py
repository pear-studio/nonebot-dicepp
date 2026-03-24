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

Code-Level Explicit Switch (design decision 2):
- Legacy path is disabled by default (_LEGACY_ENABLED = False).
- To enable legacy path for debugging/emergency rollback:
  1. Open this file and set _LEGACY_ENABLED = True.
  2. This is the ONLY way to activate legacy; no runtime config or env var.
  3. Remember to set it back to False and commit before merging to main.
"""

from ..roll_utils import RollDiceError


# =============================================================================
# Code-Level Explicit Switch — DO NOT enable in production
# =============================================================================
# Set to True ONLY for debugging or emergency legacy fallback.
# This switch is intentionally NOT configurable at runtime.
# Default MUST remain False; any PR that sets this to True must be temporary.
# =============================================================================
_LEGACY_ENABLED: bool = False


def is_legacy_enabled() -> bool:
    """Return whether the legacy engine explicit switch is currently enabled."""
    return _LEGACY_ENABLED


def assert_legacy_enabled() -> None:
    """
    Guard function: raises RuntimeError if legacy switch is not enabled.

    Call this at the top of any function that requires legacy engine access.
    This prevents accidental legacy calls from the default AST path.

    Raises:
        RuntimeError: If _LEGACY_ENABLED is False (the default).
    """
    if not _LEGACY_ENABLED:
        raise RuntimeError(
            "Legacy roll engine is disabled (roll_engine=legacy explicit switch is OFF). "
            "To enable legacy path for debugging, set _LEGACY_ENABLED = True in "
            "ast_engine/legacy_adapter.py. "
            "This should only be done temporarily for emergency rollback."
        )


def call_legacy_engine(expression: str):
    """
    Execute ``expression`` through the legacy roll engine and return a
    ``RollResult``.

    This is the *only* entry point from ``ast_engine`` into legacy code.
    Replace or stub this function to cut the legacy dependency entirely.

    ⚠️  Requires legacy explicit switch to be enabled (_LEGACY_ENABLED = True).
    The default is disabled; this function will raise RuntimeError if called
    without enabling the switch.

    Args:
        expression: Raw (already-preprocessed or raw) expression string.

    Returns:
        ``RollResult`` as produced by the legacy engine.

    Raises:
        RuntimeError: If _LEGACY_ENABLED is False.
        ``RollDiceError``: propagated unchanged so callers can decide whether
        to wrap it into an AST engine error type.
    """
    assert_legacy_enabled()  # 先校验开关，阻止误导日志产生

    import logging as _logging
    _logging.getLogger(__name__).warning(
        "roll_engine=legacy expression=%r (explicit legacy switch enabled, "
        "this path should only be used for debugging/emergency rollback)",
        expression,
    )

    from ..expression import exec_roll_exp_legacy  # deferred to avoid circular import at module load

    return exec_roll_exp_legacy(expression)


__all__ = ["call_legacy_engine", "is_legacy_enabled", "assert_legacy_enabled", "RollDiceError"]