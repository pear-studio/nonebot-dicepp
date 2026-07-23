"""Fresh-process command registration contracts."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys


def test_fresh_process_registers_only_the_new_log_command(
    pytestconfig,
) -> None:
    repository_root = Path(str(pytestconfig.rootpath))
    source_root = repository_root / "src"
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        [str(source_root), env.get("PYTHONPATH", "")]
    ).rstrip(os.pathsep)
    script = """
import json
import plugins.DicePP.module
import plugins.DicePP.module.common
from plugins.DicePP.core.command.user_cmd import DEFAULT_REGISTRY
print(json.dumps([item.__name__ for item in DEFAULT_REGISTRY.get_sorted_commands()]))
"""

    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=repository_root,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    names = json.loads(completed.stdout.strip().splitlines()[-1])

    assert names.count("LogCommand") == 1
    assert names.index("LogCommand") < names.index("QueryCommand")
