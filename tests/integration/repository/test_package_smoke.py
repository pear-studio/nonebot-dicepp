"""Windows package smoke checks.

These tests guard the PyInstaller smoke path that release builds run after
creating the onedir package.
"""

import ast
from pathlib import Path


def test_smoke_check_validates_the_managed_plugin_without_reimporting_it(
    monkeypatch,
    capsys,
):
    from plugins.DicePP import _smoke_check

    managed_plugin = object()
    received: list[object] = []
    monkeypatch.setattr(_smoke_check, "_check_frozen_env", lambda: [])
    monkeypatch.setattr(_smoke_check, "_check_critical_modules", lambda: [])
    monkeypatch.setattr(_smoke_check, "_check_version", lambda: [])

    assert _smoke_check.run_smoke_check(
        managed_plugin,
        plugin_validator=lambda plugin: received.append(plugin),
    )
    assert received == [managed_plugin]
    assert capsys.readouterr().out == (
        "\nSMOKE CHECK PASSED: "
        "plugin=plugins.DicePP.plugin matchers=nonempty registry=nonempty\n"
    )


def test_pyinstaller_spec_analyzes_the_canonical_dicepp_plugin_graph():
    spec = Path("scripts/build/dicepp.spec").read_text(encoding="utf-8")

    assert "DICEPP_PLUGIN_ENTRYPOINT = 'plugins.DicePP.plugin'" in spec
    assert 'DICEPP_PLUGIN_ENTRYPOINT,' in spec
    assert "collect_submodules('DicePP')" not in spec


def test_canonical_plugin_entrypoint_is_a_launcher_only():
    plugin_source = Path("src/plugins/DicePP/plugin.py").read_text(encoding="utf-8")
    module = ast.parse(plugin_source)
    statements = [
        statement
        for statement in module.body
        if not (
            isinstance(statement, ast.Expr)
            and isinstance(statement.value, ast.Constant)
            and isinstance(statement.value.value, str)
        )
    ]

    assert len(statements) == 1
    statement = statements[0]
    assert isinstance(statement, ast.ImportFrom)
    assert statement.module == "plugins.DicePP"
    assert [(name.name, name.asname) for name in statement.names] == [
        ("_plugin_registration", "_registration"),
    ]


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


def test_windows_build_has_no_update_guard_entrypoint() -> None:
    build = Path("scripts/build/build.bat").read_text(encoding="utf-8")
    assembly = Path("scripts/build/assemble_windows_package.ps1").read_text(
        encoding="utf-8"
    )

    assert not Path("scripts/build/update_guard.spec").exists()
    assert not Path("scripts/build/update_guard_entry.py").exists()
    assert "update_guard.spec" not in build
    assert "UpdateGuardSource" not in assembly


def test_distribution_sources_do_not_ship_global_config() -> None:
    assert not Path("config/global.json").exists()

    distribution_sources = (
        Path("Dockerfile"),
        Path("scripts/build/dicepp.spec"),
        Path("scripts/build/dashboard.spec"),
    )
    for path in distribution_sources:
        assert "global.json" not in path.read_text(encoding="utf-8")

    assembly = Path("scripts/build/assemble_windows_package.ps1").read_text(
        encoding="utf-8"
    )
    assert "Windows distribution must not contain config/global.json" in assembly
