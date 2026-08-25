"""Windows package and distribution contract checks."""

import ast
from pathlib import Path


def test_windows_package_verification_is_shared_by_build_and_test_suite():
    script = Path("scripts/build/verify_windows_package.ps1")
    assert script.is_file()
    verification = script.read_text(encoding="utf-8")
    assert "Start-Process" in verification
    assert "GetTempPath" in verification
    assert "Copy-Item" in verification
    assert "Remove-Item -LiteralPath $verifyDist" in verification
    assert "-WorkingDirectory $verifyDist" in verification

    build = Path("scripts/build/build.bat").read_text(encoding="utf-8")
    test_workflow = Path(".github/workflows/test-suite.yml").read_text(encoding="utf-8")
    release_workflow = Path(".github/workflows/release.yml").read_text(encoding="utf-8")
    removed_flag = "--" + "smoke-" + "check"
    for contract in (build, test_workflow):
        assert "verify_windows_package.ps1" in contract
        assert removed_flag not in contract
    assert "verify_windows_package.ps1" not in release_workflow
    assert removed_flag not in release_workflow


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
    assert "Portable ZIP" in build
    assert "build_windows_launcher_shim" not in assembly
    assert not Path("scripts/build/build_windows_launcher_shim.ps1").exists()
    assert not Path("scripts/build/windows_launcher_shim.cpp").exists()


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
