from __future__ import annotations

from pathlib import Path

import pytest

from tests.support.layout_policy import check_test_layout
from tools.check_test_layout import main


def _write_source(tmp_path: Path, layer: str, source: str) -> Path:
    tests_root = tmp_path / "tests"
    target = tests_root / layer / "test_sample.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(source, encoding="utf-8")
    return tests_root


def _write_internal_import_namespace_source(tmp_path: Path, source: str) -> Path:
    tests_root = tmp_path / "tests"
    target = (
        tests_root
        / "integration"
        / "repository"
        / "test_internal_import_namespace.py"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(source, encoding="utf-8")
    return tests_root


def _codes(tests_root: Path) -> set[str]:
    return {violation.code for violation in check_test_layout(tests_root)}


def test_layout_rejects_unknown_top_level_locations(tmp_path: Path) -> None:
    tests_root = _write_source(tmp_path, "legacy", "def test_old():\n    pass\n")

    violations = check_test_layout(tests_root)

    assert [(item.code, item.path.name) for item in violations] == [
        ("LAY001", "legacy"),
    ]


@pytest.mark.parametrize(
    ("source", "expected_code"),
    [
        ("import sqlite3 as sql\nsql.connect(':memory:')\n", "UNT001"),
        ("from aiosqlite import connect as open_db\nopen_db('db.sqlite')\n", "UNT001"),
        ("from nonebot.adapters import Bot as RuntimeBot\nRuntimeBot(None, None)\n", "UNT002"),
        ("from plugins.DicePP.core.bot import Bot\nBot('test')\n", "UNT002"),
        (
            "from starlette.testclient import TestClient as Client\nClient(object())\n",
            "UNT002",
        ),
        ("from subprocess import run as execute\nexecute(['worker'])\n", "SYS001"),
        (
            "import asyncio as aio\naio.create_subprocess_exec('worker')\n",
            "SYS001",
        ),
        (
            "from socket import socket as make_socket\n"
            "listener = make_socket()\n"
            "listener.bind(('127.0.0.1', 0))\n",
            "SYS002",
        ),
        (
            "from websockets.asyncio.server import serve as host\n"
            "host(lambda ws: None, 'localhost', 0)\n",
            "SYS003",
        ),
        (
            "import uvicorn as asgi\n"
            "server = asgi.Server(object())\n"
            "server.serve()\n",
            "SYS003",
        ),
        (
            "from playwright.sync_api import sync_playwright as start\n"
            "browser_type.launch()\n",
            "SYS004",
        ),
        (
            "from pathlib import Path\n"
            "target = Path('result.json')\n"
            "target.write_text('{}')\n",
            "UNT003",
        ),
    ],
)
def test_unit_layer_rejects_identifiable_real_resources(
    tmp_path: Path,
    source: str,
    expected_code: str,
) -> None:
    tests_root = _write_source(tmp_path, "unit", source)

    assert _codes(tests_root) == {expected_code}


def test_integration_allows_in_process_resources(tmp_path: Path) -> None:
    tests_root = _write_source(
        tmp_path,
        "integration",
        "import sqlite3\n"
        "from fastapi.testclient import TestClient\n"
        "sqlite3.connect(':memory:')\n"
        "TestClient(object())\n",
    )

    assert check_test_layout(tests_root) == []


@pytest.mark.parametrize(
    "source",
    [
        "import subprocess as process\nprocess.run(['worker'])\n",
        "import socket\nserver = socket.socket()\nserver.listen()\n",
        "import uvicorn\nuvicorn.run(object())\n",
        "import playwright.async_api\nbrowser.launch()\n",
    ],
)
def test_integration_rejects_system_resources(tmp_path: Path, source: str) -> None:
    tests_root = _write_source(tmp_path, "integration", source)

    assert len(check_test_layout(tests_root)) == 1
    assert next(iter(_codes(tests_root))).startswith("SYS")


def test_system_layer_allows_process_and_browser_resources(tmp_path: Path) -> None:
    tests_root = _write_source(
        tmp_path,
        "system",
        "import subprocess\n"
        "import playwright.sync_api\n"
        "subprocess.run(['worker'])\n"
        "browser.launch()\n",
    )

    assert check_test_layout(tests_root) == []


@pytest.mark.parametrize(
    "source",
    [
        "import conftest as fixtures\n",
        "from tests.conftest import configured_bot\n",
        "from . import conftest\n",
    ],
)
def test_all_layers_reject_importing_conftest(tmp_path: Path, source: str) -> None:
    tests_root = _write_source(tmp_path, "support", source)

    assert _codes(tests_root) == {"IMP001"}


@pytest.mark.parametrize(
    "source",
    [
        "from pathlib import Path as LocalPath\nROOT = LocalPath(__file__).parents[2]\n",
        "import pathlib as paths\nROOT = paths.Path(__file__).resolve().parents[1]\n",
    ],
)
def test_all_layers_reject_fragile_repository_lookup(
    tmp_path: Path,
    source: str,
) -> None:
    tests_root = _write_source(tmp_path, "support", source)

    assert _codes(tests_root) == {"PTH001"}


@pytest.mark.parametrize(
    ("layer", "source"),
    [
        (
            "integration",
            "import sys\n"
            "from pathlib import Path\n"
            "legacy_root = Path('repo') / 'src' / 'plugins'\n"
            "sys.path.insert(0, str(legacy_root))\n",
        ),
        ("integration",
            "import sys\n"
            "from pathlib import Path\n"
            "package_root = Path('repo') / 'src' / 'plugins' / 'DicePP'\n"
            "sys.path.insert(0, str(package_root))\n"
        ),
        (
            "integration",
            "import sys\n"
            "from pathlib import Path\n"
            "legacy_descendant = (\n"
            "    Path('repo') / 'src' / 'plugins' / 'DicePP' / 'core'\n"
            ")\n"
            "sys.path.append(str(legacy_descendant))\n",
        ),
        ("integration",
            "import sys\n"
            "from pathlib import Path\n"
            "package_root = Path('repo') / 'src' / 'plugins' / 'DicePP'\n"
            "sys.path.append(str(package_root))\n"
        ),
        ("integration",
            "import sys\n"
            "from pathlib import Path\n"
            "package_root = Path('repo') / 'src' / 'plugins' / 'DicePP'\n"
            "sys.path.extend([str(package_root)])\n"
        ),
        (
            "integration",
            "import os\n"
            "from pathlib import Path\n"
            "legacy_root = Path('repo') / 'src' / 'plugins'\n"
            "env = {}\n"
            "env['PYTHONPATH'] = os.pathsep.join([str(legacy_root), ''])\n",
        ),
        (
            "integration",
            "import os\n"
            "os.environ['PYTHONPATH'] = r'C:\\repo\\src\\plugins;C:\\other'\n",
        ),
        ("integration",
            "import os\n"
            "from pathlib import Path\n"
            "package_root = Path('repo') / 'src' / 'plugins' / 'DicePP'\n"
            "env = {}\n"
            "env['PYTHONPATH'] = os.pathsep.join([str(package_root), ''])\n"
        ),
        ("system",
            "import sys\n"
            "repo = 'repo'\n"
            "sys.path.insert(0, f'{repo}/src/plugins/DicePP')\n"
        ),
        ("system",
            "import subprocess\n"
            "from pathlib import Path\n"
            "package_root = Path('repo') / 'src' / 'plugins' / 'DicePP'\n"
            "subprocess.run(['worker'], env={'PYTHONPATH': str(package_root)})\n"
        ),
        ("system",
            "import os\n"
            "import subprocess\n"
            "from pathlib import Path\n"
            "package_root = Path('repo') / 'src' / 'plugins' / 'DicePP'\n"
            "subprocess.run(\n"
            "    ['worker'],\n"
            "    env={**os.environ, 'PYTHONPATH': str(package_root)},\n"
            ")\n"
        ),
        ("system",
            "import subprocess\n"
            "from pathlib import Path\n"
            "package_root = Path('repo') / 'src' / 'plugins' / 'DicePP'\n"
            "env = {'PYTHONPATH': str(package_root)}\n"
            "subprocess.run(['worker'], env=env)\n"
        ),
        ("system",
            "import os\n"
            "import subprocess\n"
            "from pathlib import Path\n"
            "package_root = Path('repo') / 'src' / 'plugins' / 'DicePP'\n"
            "env = {**os.environ, 'PYTHONPATH': str(package_root)}\n"
            "subprocess.run(['worker'], env=env)\n"
        ),
        ("system",
            "import os\n"
            "import subprocess\n"
            "from pathlib import Path\n"
            "package_root = Path('repo') / 'src' / 'plugins' / 'DicePP'\n"
            "env = os.environ | {'PYTHONPATH': str(package_root)}\n"
            "subprocess.run(['worker'], env=env)\n"
        ),
        ("system",
            "import os\n"
            "import subprocess\n"
            "from pathlib import Path\n"
            "package_root = Path('repo') / 'src' / 'plugins' / 'DicePP'\n"
            "env = dict(os.environ, PYTHONPATH=str(package_root))\n"
            "subprocess.run(['worker'], env=env)\n"
        ),
        (
            "integration",
            "import sys\n"
            "from pathlib import Path\n"
            "legacy_root = (Path('repo') / 'src' / 'plugins').resolve()\n"
            "sys.path.insert(0, str(legacy_root))\n",
        ),
        (
            "integration",
            "import sys\n"
            "from pathlib import Path\n"
            "legacy_root = (\n"
            "    Path('repo') / 'src' / 'plugins' / 'DicePP' / 'core'\n"
            ").absolute()\n"
            "sys.path.insert(0, str(legacy_root))\n",
        ),
        (
            "integration",
            "import os\n"
            "import sys\n"
            "legacy_root = os.path.abspath('repo/src/plugins/DicePP')\n"
            "sys.path.insert(0, legacy_root)\n",
        ),
        (
            "integration",
            "import os\n"
            "import sys\n"
            "legacy_root = os.path.normpath('repo/src/plugins/DicePP/core')\n"
            "sys.path.insert(0, legacy_root)\n",
        ),
        (
            "integration",
            "import os\n"
            "os.environ['PYTHONPATH'] = 'repo/src/plugins/../plugins'\n",
        ),
        (
            "integration",
            "import sys\n"
            "from pathlib import Path\n"
            "legacy_root = (\n"
            "    Path('repo') / 'src' / 'plugins' / 'DicePP' / 'core'\n"
            ").resolve().parent\n"
            "sys.path.insert(0, str(legacy_root))\n",
        ),
    ],
)
def test_test_layers_reject_dicepp_legacy_import_path_exposure(
    tmp_path: Path,
    layer: str,
    source: str,
) -> None:
    tests_root = _write_source(tmp_path, layer, source)

    assert _codes(tests_root) == {"PTH002"}


def test_test_layers_allow_unrelated_plugin_package_paths(tmp_path: Path) -> None:
    tests_root = _write_source(
        tmp_path,
        "integration",
        "import os\n"
        "import sys\n"
        "from pathlib import Path\n"
        "other_plugin = (\n"
        "    Path('repo') / 'src' / 'plugins' / 'other_plugin' / '..' / 'other_plugin'\n"
        ").resolve()\n"
        "sys.path.insert(0, str(other_plugin))\n"
        "os.environ['PYTHONPATH'] = '/repo/src/plugins/other_plugin'\n",
    )

    assert check_test_layout(tests_root) == []


def test_system_process_setup_allows_canonical_src_path(tmp_path: Path) -> None:
    tests_root = _write_source(
        tmp_path,
        "system",
        "import os\n"
        "import subprocess\n"
        "import sys\n"
        "from pathlib import Path\n"
        "source_root = Path('repo') / 'src'\n"
        "sys.path.insert(0, str(source_root))\n"
        "env = {}\n"
        "env['PYTHONPATH'] = os.pathsep.join([str(source_root), ''])\n"
        "subprocess.run(['worker'], env={'PYTHONPATH': str(source_root)})\n"
        "subprocess.run(\n"
        "    ['worker'],\n"
        "    env={**os.environ, 'PYTHONPATH': str(source_root)},\n"
        ")\n"
        "named_env = {'PYTHONPATH': str(source_root)}\n"
        "subprocess.run(['worker'], env=named_env)\n"
        "spread_env = {**os.environ, 'PYTHONPATH': str(source_root)}\n"
        "subprocess.run(['worker'], env=spread_env)\n"
        "merged_env = os.environ | {'PYTHONPATH': str(source_root)}\n"
        "subprocess.run(['worker'], env=merged_env)\n"
        "dict_env = dict(os.environ, PYTHONPATH=str(source_root))\n"
        "subprocess.run(['worker'], env=dict_env)\n",
    )

    assert check_test_layout(tests_root) == []


@pytest.mark.parametrize("layer", ["unit", "integration", "support", "system"])
@pytest.mark.parametrize(
    "source",
    [
        "from utils.time import wall_now\n",
        "import module.persona.factory\n",
        "import DicePP.core.command\n",
        "from DicePP.core import command\n",
        (
            "from unittest.mock import patch\n"
            "patch('utils.time.wall_now')\n"
        ),
    ],
)
def test_test_layers_reject_legacy_dicepp_imports(
    tmp_path: Path,
    layer: str,
    source: str,
) -> None:
    tests_root = _write_source(tmp_path, layer, source)

    assert _codes(tests_root) == {"IMP002"}


@pytest.mark.parametrize(
    "source",
    [
        (
            "import importlib\n"
            "target = 'core.command'\n"
            "importlib.import_module(target)\n"
        ),
        (
            "from unittest.mock import patch\n"
            "target = 'utils.time.wall_now'\n"
            "patch(target)\n"
        ),
        (
            "import importlib\n"
            "target = 'DicePP.core.command'\n"
            "importlib.import_module(target)\n"
        ),
        (
            "from unittest.mock import patch\n"
            "target = 'DicePP.core.command.execute'\n"
            "patch(target)\n"
        ),
        (
            "target = 'adapter.client_proxy.ClientProxy'\n"
            "monkeypatch.setattr(target, object())\n"
        ),
    ],
)
def test_test_layers_reject_legacy_dicepp_dynamic_target_variables(
    tmp_path: Path,
    source: str,
) -> None:
    tests_root = _write_source(tmp_path, "integration", source)

    assert _codes(tests_root) == {"IMP002"}


def test_layout_allows_only_the_declared_legacy_import_failure_probe(
    tmp_path: Path,
) -> None:
    source = (
        "import importlib\n"
        "import pytest\n"
        "LEGACY_BARE_IMPORT_FAILURE_PROBE = 'core.command'\n"
        "LEGACY_TOP_LEVEL_PACKAGE_IMPORT_FAILURE_PROBE = 'DicePP.core.command'\n"
        "with pytest.raises(ModuleNotFoundError):\n"
        "    importlib.import_module(LEGACY_BARE_IMPORT_FAILURE_PROBE)\n"
        "with pytest.raises(ModuleNotFoundError):\n"
        "    importlib.import_module(LEGACY_TOP_LEVEL_PACKAGE_IMPORT_FAILURE_PROBE)\n"
    )
    tests_root = _write_internal_import_namespace_source(tmp_path, source)

    assert check_test_layout(tests_root) == []


def test_layout_rejects_an_unscoped_legacy_target_in_the_probe_file(
    tmp_path: Path,
) -> None:
    source = (
        "import importlib\n"
        "import pytest\n"
        "LEGACY_BARE_IMPORT_FAILURE_PROBE = 'core.command'\n"
        "LEGACY_TOP_LEVEL_PACKAGE_IMPORT_FAILURE_PROBE = 'DicePP.core.command'\n"
        "with pytest.raises(ModuleNotFoundError):\n"
        "    importlib.import_module(LEGACY_BARE_IMPORT_FAILURE_PROBE)\n"
        "with pytest.raises(ModuleNotFoundError):\n"
        "    importlib.import_module(LEGACY_TOP_LEVEL_PACKAGE_IMPORT_FAILURE_PROBE)\n"
        "target = 'DicePP.core.command'\n"
        "importlib.import_module(target)\n"
    )
    tests_root = _write_internal_import_namespace_source(tmp_path, source)

    assert _codes(tests_root) == {"IMP002"}


def test_layout_rejects_a_declared_probe_outside_its_failure_assertion(
    tmp_path: Path,
) -> None:
    source = (
        "import importlib\n"
        "LEGACY_BARE_IMPORT_FAILURE_PROBE = 'core.command'\n"
        "result = importlib.import_module(LEGACY_BARE_IMPORT_FAILURE_PROBE)\n"
    )
    tests_root = _write_internal_import_namespace_source(tmp_path, source)

    assert _codes(tests_root) == {"IMP002"}


def test_layout_rejects_a_declared_probe_with_the_wrong_failure_type(
    tmp_path: Path,
) -> None:
    source = (
        "import importlib\n"
        "import pytest\n"
        "LEGACY_BARE_IMPORT_FAILURE_PROBE = 'core.command'\n"
        "with pytest.raises(ImportError):\n"
        "    importlib.import_module(LEGACY_BARE_IMPORT_FAILURE_PROBE)\n"
    )
    tests_root = _write_internal_import_namespace_source(tmp_path, source)

    assert _codes(tests_root) == {"IMP002"}


def test_layout_rejects_a_declared_probe_assignment_inside_failure_assertion(
    tmp_path: Path,
) -> None:
    source = (
        "import importlib\n"
        "import pytest\n"
        "LEGACY_BARE_IMPORT_FAILURE_PROBE = 'core.command'\n"
        "with pytest.raises(ModuleNotFoundError):\n"
        "    result = importlib.import_module(LEGACY_BARE_IMPORT_FAILURE_PROBE)\n"
    )
    tests_root = _write_internal_import_namespace_source(tmp_path, source)

    assert _codes(tests_root) == {"IMP002"}


def test_system_layer_allows_canonical_dicepp_imports(
    tmp_path: Path,
) -> None:
    tests_root = _write_source(
        tmp_path,
        "system",
        "from plugins.DicePP.shell.main import main\n",
    )

    assert check_test_layout(tests_root) == []


def test_dashboard_integration_allows_canonical_dicepp_imports(
    tmp_path: Path,
) -> None:
    tests_root = tmp_path / "tests"
    target = tests_root / "integration" / "dashboard" / "test_boundary.py"
    target.parent.mkdir(parents=True)
    target.write_text(
        "from plugins.DicePP.module.dashboard_reporter.protocol import encode\n",
        encoding="utf-8",
    )

    assert check_test_layout(tests_root) == []


def test_quick_marker_is_restricted_to_fast_layers(tmp_path: Path) -> None:
    tests_root = _write_source(
        tmp_path,
        "system",
        "import pytest\npytestmark = pytest.mark.quick\n",
    )

    assert _codes(tests_root) == {"QCK001"}


def test_quick_marker_is_allowed_in_unit_and_integration(tmp_path: Path) -> None:
    tests_root = _write_source(
        tmp_path,
        "unit",
        "import pytest as pt\npytestmark = pt.mark.quick\n",
    )
    _write_source(tmp_path, "integration", "import pytest\npytestmark = pytest.mark.quick\n")

    assert check_test_layout(tests_root) == []


@pytest.mark.parametrize("marker", ["unit", "integration", "e2e", "slow", "real_llm"])
def test_legacy_project_selection_markers_are_rejected(
    tmp_path: Path,
    marker: str,
) -> None:
    tests_root = _write_source(
        tmp_path,
        "unit",
        f"import pytest\npytestmark = pytest.mark.{marker}\n",
    )

    assert _codes(tests_root) == {"MRK001"}


def test_cli_help_is_available(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["--help"])

    assert exit_info.value.code == 0
    assert "test layout" in capsys.readouterr().out.lower()


def test_cli_checks_an_explicit_path(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    tests_root = _write_source(tmp_path, "unit", "import sqlite3\nsqlite3.connect('db.sqlite')\n")

    assert main([str(tests_root)]) == 1
    output = capsys.readouterr().out
    assert "UNT001" in output
    assert f"{Path('unit') / 'test_sample.py'}:2" in output


def test_repository_test_layout_conforms(pytestconfig: pytest.Config) -> None:
    tests_root = Path(str(pytestconfig.rootpath)) / "tests"

    violations = check_test_layout(tests_root)

    assert violations == [], "\n" + "\n".join(
        violation.render(tests_root) for violation in violations
    )
