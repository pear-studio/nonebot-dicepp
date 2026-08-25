import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap


def _repository_root() -> Path:
    return next(
        parent
        for parent in Path(__file__).resolve().parents
        if (parent / "pyproject.toml").is_file()
    )


def test_dicepp_app_dir_sets_config_data_path(tmp_path):
    """A fresh interpreter resolves config paths from the project-root override."""
    app_root = tmp_path / "dicepp_app_root"
    app_root.mkdir()
    dicepp_src = _repository_root() / "src"
    script = f"""
import os, sys
sys.path.insert(0, {str(dicepp_src)!r})
import plugins.DicePP.core.config.basic as basic_mod
expected_root = os.path.abspath({str(app_root)!r})
assert str(basic_mod.Paths.PROJECT_ROOT) == expected_root, (str(basic_mod.Paths.PROJECT_ROOT), expected_root)
assert str(basic_mod.Paths.CONFIG_DIR) == os.path.join(expected_root, "config"), str(basic_mod.Paths.CONFIG_DIR)
"""
    env = os.environ.copy()
    env["DICEPP_PROJECT_ROOT"] = str(app_root)
    proc = subprocess.run(
        [sys.executable, "-c", script],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_frozen_bootstrap_does_not_require_a_copied_src_tree():
    bot_source = (_repository_root() / "bot.py").read_text(encoding="utf-8")

    assert "os.path.join(exe_dir, '_internal', 'src')" not in bot_source
    assert "PyInstaller's importer/embedded PYZ" in bot_source


def test_frozen_dashboard_entry_pins_portable_environment(tmp_path: Path):
    """A frozen launcher must keep Dashboard and inherited Bot paths together."""
    project_root = _repository_root()
    portable_root = tmp_path / "portable"
    portable_root.mkdir()
    stale_root = tmp_path / "stale"
    stale_root.mkdir()
    bootstrap = textwrap.dedent(
        """
        import json
        import os
        import runpy
        import sys
        import types

        sys.frozen = True
        sys.executable = os.environ["DICEPP_TEST_EXE"]
        launcher = types.ModuleType("dashboard.src.launcher")
        launcher.main = lambda: None
        sys.modules["dashboard.src.launcher"] = launcher
        runpy.run_path(os.environ["DICEPP_TEST_ENTRY"], run_name="entry_test")
        print(json.dumps({
            key: os.environ[key]
            for key in (
                "DICEPP_APP_DIR",
                "DICEPP_PROJECT_ROOT",
                "DICEPP_DATA_DIR",
                "DICEPP_RUNTIME_LOG",
                "DASHBOARD_HOST",
                "DASHBOARD_PORT",
            )
        }))
        """
    )
    env = os.environ.copy()
    env.update(
        {
            "DICEPP_TEST_ENTRY": str(project_root / "scripts" / "build" / "dashboard_entry.py"),
            "DICEPP_TEST_EXE": str(portable_root / "DicePP.exe"),
            "DICEPP_APP_DIR": str(stale_root),
            "DICEPP_PROJECT_ROOT": str(stale_root),
            "DICEPP_DATA_DIR": str(stale_root / "wrong-data"),
            "DICEPP_RUNTIME_LOG": str(stale_root / "wrong.log"),
            "DASHBOARD_HOST": "0.0.0.0",
            "DASHBOARD_PORT": "4999",
        }
    )
    completed = subprocess.run(
        [sys.executable, "-c", bootstrap],
        cwd=project_root,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    expected_root = str(portable_root.resolve())
    assert json.loads(completed.stdout) == {
        "DICEPP_APP_DIR": expected_root,
        "DICEPP_PROJECT_ROOT": expected_root,
        "DICEPP_DATA_DIR": str(portable_root / "data"),
        "DICEPP_RUNTIME_LOG": str(
            portable_root / "data" / "logs" / "dicepp-runtime.log"
        ),
        "DASHBOARD_HOST": "127.0.0.1",
        "DASHBOARD_PORT": "4090",
    }
