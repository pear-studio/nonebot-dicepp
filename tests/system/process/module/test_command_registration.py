"""Fresh-process command registration contracts."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


def test_fresh_process_plugin_entrypoint_registers_business_commands(
    tmp_path: Path,
    pytestconfig,
) -> None:
    """The managed plugin entrypoint owns the command-module registration chain."""
    repository_root = Path(str(pytestconfig.rootpath)).resolve()
    source_root = repository_root / "src"
    runtime_root = tmp_path / "runtime"
    shutil.copytree(repository_root / "config", runtime_root / "config")
    env = os.environ.copy()
    env["DICEPP_PROJECT_ROOT"] = str(runtime_root)
    env["DICEPP_APP_DIR"] = str(runtime_root)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(source_root), env.get("PYTHONPATH", "")]
    ).rstrip(os.pathsep)
    script = """
import json

import nonebot
from nonebot.adapters.onebot.v11 import Adapter as OneBot_V11_Adapter

nonebot.init(command_start={""}, command_sep={""})
nonebot.get_driver().register_adapter(OneBot_V11_Adapter)

from plugins.DicePP.runtime_preflight import load_and_validate_dicepp_plugin

plugin = load_and_validate_dicepp_plugin()
from plugins.DicePP.core.command.user_cmd import DEFAULT_REGISTRY

print(json.dumps({
    "module_name": plugin.module_name,
    "commands": [item.__name__ for item in DEFAULT_REGISTRY.get_sorted_commands()],
}))
"""

    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=runtime_root,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )

    output = completed.stdout + completed.stderr
    assert completed.returncode == 0, output
    payload = json.loads(completed.stdout.strip().splitlines()[-1])
    names = payload["commands"]

    assert payload["module_name"] == "plugins.DicePP.plugin"
    assert names.count("LogCommand") == 1
    assert names.index("LogCommand") < names.index("QueryCommand")
