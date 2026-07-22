"""Subprocess contract tests for the packaged Dashboard launcher entry."""

import json
import subprocess
import sys
import textwrap
from pathlib import Path

from tests.support.dashboard.paths import repo_root


def test_dashboard_entry_preconfigures_env_before_dashboard_import(
    tmp_path: Path,
) -> None:
    app_dir = tmp_path / "DicePP"
    app_dir.mkdir()
    project_root = repo_root()
    code = textwrap.dedent(
        f"""
        import json
        import os
        import runpy
        import sys

        for key in (
            "DICEPP_PROJECT_ROOT",
            "DASHBOARD_HOST",
            "DASHBOARD_PORT",
            "DICEPP_MANAGER_RUNTIME",
            "DICEPP_MANAGER_PROCESS_COMMAND",
            "DICEPP_MANAGER_PROCESS_CWD",
        ):
            os.environ.pop(key, None)

        sys.frozen = True
        sys.executable = {str(app_dir / "DicePP.exe")!r}
        runpy.run_path(
            {str(project_root / "scripts" / "build" / "dashboard_entry.py")!r},
            run_name="dashboard_entry_test",
        )

        from dashboard.src.config import DashboardPaths

        print(json.dumps({{
            "project_root": str(DashboardPaths.PROJECT_ROOT),
            "runtime_log": str(DashboardPaths.RUNTIME_LOG),
            "env_root": os.environ["DICEPP_PROJECT_ROOT"],
            "runtime_command": os.environ["DICEPP_MANAGER_PROCESS_COMMAND"],
        }}))
        """
    )

    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=project_root,
        capture_output=True,
        text=True,
        check=True,
    )

    payload = json.loads(result.stdout)
    assert Path(payload["project_root"]) == app_dir
    assert Path(payload["runtime_log"]) == app_dir / "data" / "logs" / "dicepp-runtime.log"
    assert Path(payload["env_root"]) == app_dir
    assert "DicePP-Runtime.exe" in payload["runtime_command"]
