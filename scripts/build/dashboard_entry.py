"""PyInstaller bootstrap for the Windows DicePP single-entry executable."""

import os
import sys


def _quote_command(parts: list[str]) -> str:
    if os.name == "nt":
        import subprocess

        return subprocess.list2cmdline(parts)
    import shlex

    return " ".join(shlex.quote(part) for part in parts)


def _configure_launcher_environment(
    app_dir: str,
    *,
    runtime_exe_name: str = "DicePP-Runtime.exe",
) -> dict[str, str]:
    runtime_path = os.path.join(app_dir, runtime_exe_name)
    defaults = {
        "DICEPP_PROJECT_ROOT": app_dir,
        "DASHBOARD_HOST": "127.0.0.1",
        "DASHBOARD_PORT": "4090",
        "DICEPP_MANAGER_RUNTIME": "process",
        "DICEPP_MANAGER_PROCESS_COMMAND": _quote_command([runtime_path]),
        "DICEPP_MANAGER_PROCESS_CWD": app_dir,
    }
    for key, value in defaults.items():
        os.environ.setdefault(key, value)
    return {key: os.environ[key] for key in defaults}


project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

if getattr(sys, "frozen", False):
    app_dir = os.path.dirname(sys.executable)
    os.chdir(app_dir)
else:
    app_dir = project_root


_configure_launcher_environment(app_dir)

from dashboard.src.launcher import main


if __name__ == "__main__":
    main()
