"""PyInstaller bootstrap for the Windows Portable Dashboard executable."""

from __future__ import annotations

import os
import sys


def _ensure_windowed_standard_streams() -> None:
    """Give a windowed PyInstaller process harmless output streams."""
    for name in ("stdout", "stderr"):
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8", buffering=1))


def _configure_launcher_environment(app_dir: str) -> dict[str, str]:
    """Use the Portable directory as both application and instance root."""
    root = os.path.abspath(app_dir)
    defaults = {
        "DICEPP_APP_DIR": root,
        "DICEPP_PROJECT_ROOT": root,
        "DASHBOARD_HOST": "127.0.0.1",
        "DASHBOARD_PORT": "4090",
    }
    for key, value in defaults.items():
        os.environ.setdefault(key, value)
    return {key: os.environ[key] for key in defaults}


project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

_ensure_windowed_standard_streams()
app_dir = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else project_root
_launcher_environment = _configure_launcher_environment(app_dir)
os.chdir(_launcher_environment["DICEPP_PROJECT_ROOT"])

from dashboard.src.launcher import main


if __name__ == "__main__":
    main()
