import os
from pathlib import Path
import subprocess
import sys


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
