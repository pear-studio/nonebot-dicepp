"""PyInstaller bootstrap for the standalone Dashboard executable."""

import os
import sys


if getattr(sys, "frozen", False):
    app_dir = os.path.dirname(sys.executable)
    os.chdir(app_dir)
    os.environ.setdefault("DICEPP_PROJECT_ROOT", app_dir)
    os.environ.setdefault("DASHBOARD_HOST", "127.0.0.1")

from dashboard.__main__ import main


if __name__ == "__main__":
    main()
