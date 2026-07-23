"""Windows package smoke checks.

These tests guard the PyInstaller smoke path that release builds run after
creating the onedir package.
"""

from pathlib import Path

import pytest


def test_smoke_check_imports_dicepp_plugin_entrypoint():
    from plugins.DicePP._smoke_check import _check_dicepp_plugin_import

    assert _check_dicepp_plugin_import() == []


def test_pyinstaller_spec_collects_cryptography_submodules():
    spec = Path("scripts/build/dicepp.spec").read_text(encoding="utf-8")

    assert "collect_submodules('cryptography')" in spec


def test_pyinstaller_spec_collects_shared_project_metadata():
    spec = Path("scripts/build/dicepp.spec").read_text(encoding="utf-8")

    assert "collect_submodules('dicepp_meta')" in spec
    assert "os.path.join(PROJECT_ROOT, 'src')" in spec
