"""Source entrypoint checks."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
import textwrap


def _isolated_runtime_environment(
    project_root: Path,
    tmp_path: Path,
) -> tuple[Path, dict[str, str]]:
    """Give a runtime probe a private cwd, config root, and log directory."""
    runtime_root = tmp_path / "runtime"
    shutil.copytree(project_root / "config", runtime_root / "config")
    env = os.environ.copy()
    env["DICEPP_PROJECT_ROOT"] = str(runtime_root)
    env["DICEPP_APP_DIR"] = str(runtime_root)
    return runtime_root, env


def test_source_version_is_metadata_only(
    tmp_path: Path,
    pytestconfig,
) -> None:
    """Version lookup exits before any DicePP or NoneBot runtime import."""
    project_root = Path(str(pytestconfig.rootpath)).resolve()
    runtime_root, env = _isolated_runtime_environment(project_root, tmp_path)
    bootstrap = textwrap.dedent(
        """
        import runpy
        import sys
        from pathlib import Path

        project_root = Path(sys.argv[1]).resolve()
        sys.argv = ["bot.py", "--version"]
        try:
            runpy.run_path(str(project_root / "bot.py"), run_name="__main__")
        except SystemExit as exc:
            assert exc.code == 0, exc.code
        else:
            raise AssertionError("bot.py --version did not exit")

        assert not any(
            module_name == "nonebot" or module_name.startswith("nonebot.")
            for module_name in sys.modules
        )
        assert not any(
            module_name == "plugins.DicePP"
            or module_name.startswith("plugins.DicePP.")
            for module_name in sys.modules
        )
        """
    )
    result = subprocess.run(
        [sys.executable, "-I", "-c", bootstrap, str(project_root)],
        cwd=runtime_root,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )

    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "DicePP v" in output
