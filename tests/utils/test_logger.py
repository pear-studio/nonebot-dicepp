import os
import subprocess
import sys

import pytest


pytestmark = pytest.mark.unit


def test_bot_import_logs_tolerate_nonebot_reconfigure():
    """NoneBot startup logs should not break handlers that include request_id."""
    proc = subprocess.run(
        [sys.executable, "-c", "import bot; print('imported')"],
        cwd=os.getcwd(),
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "imported" in proc.stdout
    assert "--- Logging error" not in proc.stderr
    assert "KeyError: 'request_id'" not in proc.stderr
