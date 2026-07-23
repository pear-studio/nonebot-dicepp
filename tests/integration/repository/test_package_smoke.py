"""Windows package smoke checks.

These tests guard the PyInstaller smoke path that release builds run after
creating the onedir package.
"""

from pathlib import Path


def test_smoke_check_imports_dicepp_plugin_entrypoint():
    from plugins.DicePP._smoke_check import _check_dicepp_plugin_import

    assert _check_dicepp_plugin_import() == []


def test_pyinstaller_spec_analyzes_the_canonical_dicepp_plugin_graph():
    spec = Path("scripts/build/dicepp.spec").read_text(encoding="utf-8")

    assert "DICEPP_PLUGIN_ENTRYPOINT = 'plugins.DicePP.plugin'" in spec
    assert 'DICEPP_PLUGIN_ENTRYPOINT,' in spec
    assert "collect_submodules('DicePP')" not in spec


def test_pyinstaller_spec_keeps_implementation_modules_out_of_datas_except_launcher():
    spec = Path("scripts/build/dicepp.spec").read_text(encoding="utf-8")

    assert "DICEPP_PLUGIN_LAUNCHER = DICEPP_PACKAGE_DIR / 'plugin.py'" in spec
    assert "(str(DICEPP_PLUGIN_LAUNCHER), os.path.join('plugins', 'DicePP'))" in spec
    assert 'datas += collect_dicepp_resources()' in spec
    assert "DICEPP_PACKAGE_DIR.rglob('*')" in spec
    assert 'resource.suffix not in ALL_SUFFIXES' in spec
    assert "collect_submodules('cryptography')" not in spec
    assert "collect_submodules('dicepp_meta')" not in spec
    assert "os.path.join(PROJECT_ROOT, 'src')" in spec
    assert "os.path.join(PROJECT_ROOT, 'src', 'plugins', 'DicePP')" not in spec
