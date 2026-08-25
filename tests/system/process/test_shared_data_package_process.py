from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_shared_data_package_import_does_not_start_nonebot(pytestconfig) -> None:
    root = Path(str(pytestconfig.rootpath))
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        [str(root / "src"), env.get("PYTHONPATH", "")]
    )
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import dicepp_data; "
            "assert 'nonebot' not in sys.modules",
        ],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_bot_and_dashboard_facades_resolve_the_same_instance(
    tmp_path,
    pytestconfig,
) -> None:
    root = Path(str(pytestconfig.rootpath))
    instance = tmp_path / "instance"
    data = tmp_path / "data-override"
    env = os.environ.copy()
    env["DICEPP_PROJECT_ROOT"] = str(instance)
    env["DICEPP_DATA_DIR"] = str(data)
    env["PYTHONPATH"] = os.pathsep.join(
        [
            str(root),
            str(root / "src"),
        ]
    )
    code = (
        "from plugins.DicePP.core.config.basic import Paths; "
        "from dashboard.src.config import DashboardPaths; "
        "assert Paths.PROJECT_ROOT == DashboardPaths.PROJECT_ROOT; "
        "assert Paths.DATA_DIR == DashboardPaths.DATA_ROOT; "
        "assert Paths.CONFIG_USER == DashboardPaths.CONFIG_USER; "
        "assert Paths.CONTENT_DIR == DashboardPaths.CONTENT_DIR"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert result.returncode == 0, result.stderr
