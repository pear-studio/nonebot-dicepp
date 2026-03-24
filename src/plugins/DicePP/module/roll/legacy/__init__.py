"""
Legacy Roll Engine — Isolated Implementation
=============================================

This package marks the boundary of legacy roll engine code.

The legacy roll engine is the original regex/recursive-descent parser
implementation. It is **not** the default execution path.

Usage (emergency rollback only):
- Enable: set `_LEGACY_ENABLED = True` in `ast_engine/legacy_adapter.py`
- Call: `exec_roll_exp_legacy()` from `module.roll.expression`

⚠️  Do NOT import from this package in default AST execution paths.
"""

# Re-export the legacy execution entry point for explicit use only.
# This file is the "official" legacy package marker — any code needing
# legacy engine access should import from here (or via legacy_adapter).

from module.roll.expression import exec_roll_exp_legacy  # noqa: F401

__all__ = ["exec_roll_exp_legacy"]
