from __future__ import annotations

import pytest

from scripts.build.windows_upgrade_matrix_harness import _validate_context


def _context() -> dict:
    return {
        "contract_version": 1,
        "platform": "windows",
        "arch": "amd64",
        "source_version": "3.0.0rc19",
        "scenario": "healthy_commit",
        "source_assets": [{"purpose": "portable"}],
        "target_version": "3.1.0",
        "target_commit_sha": "a" * 40,
        "target_assets": [{"purpose": "velopack-bundle"}],
    }


def test_context_accepts_closed_windows_identity() -> None:
    _validate_context(_context())


def test_context_rejects_platform_or_field_drift() -> None:
    context = _context()
    context["platform"] = "linux"
    with pytest.raises(ValueError, match="another context"):
        _validate_context(context)

    context = _context()
    context["extra"] = True
    with pytest.raises(ValueError, match="do not match"):
        _validate_context(context)
