"""The frozen Dashboard schema assets work without the repository source tree."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from tests.support.paths import find_repository_root


ROOT = find_repository_root(Path(__file__))
SCHEMA_DIR = ROOT / "src" / "plugins" / "DicePP" / "core" / "config"


def test_standalone_schema_assets_construct_without_runtime_package(
    tmp_path: Path,
) -> None:
    assets = tmp_path / "dashboard_config_schema"
    assets.mkdir()
    shutil.copyfile(SCHEMA_DIR / "pydantic_models.py", assets / "pydantic_models.py")

    script = r'''
import importlib.util
import pathlib
import sys
import types

root = pathlib.Path(sys.argv[1])
package_name = "isolated_dashboard_schema"
package = types.ModuleType(package_name)
package.__path__ = [str(root)]
package.__package__ = package_name
sys.modules[package_name] = package
spec = importlib.util.spec_from_file_location(
    package_name + ".pydantic_models", root / "pydantic_models.py"
)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
config = module.UserConfig()
assert config.deepseek_model == "deepseek-v4-flash"
assert module.BotConfig().persona_ai.enabled is True
dumped = config.model_dump(mode="json", by_alias=True)
assert dumped["deepseek_base_url"] == "https://api.deepseek.com"
'''
    completed = subprocess.run(
        [sys.executable, "-I", "-c", script, str(assets)],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
