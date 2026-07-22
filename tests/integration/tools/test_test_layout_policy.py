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
        ("from core.bot import Bot\nBot('test')\n", "UNT002"),
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


@pytest.mark.parametrize("layer", ["unit", "integration", "support"])
@pytest.mark.parametrize(
    "source",
    [
        "from plugins.DicePP.utils.time import wall_now\n",
        "import plugins.DicePP.module.persona.factory\n",
        (
            "from unittest.mock import patch\n"
            "patch('plugins.DicePP.utils.time.wall_now')\n"
        ),
    ],
)
def test_in_process_layers_reject_package_qualified_dicepp_imports(
    tmp_path: Path,
    layer: str,
    source: str,
) -> None:
    tests_root = _write_source(tmp_path, layer, source)

    assert _codes(tests_root) == {"IMP002"}


def test_system_layer_allows_package_qualified_boundary_imports(
    tmp_path: Path,
) -> None:
    tests_root = _write_source(
        tmp_path,
        "system",
        "from plugins.DicePP.shell.main import main\n",
    )

    assert check_test_layout(tests_root) == []


def test_dashboard_integration_allows_its_explicit_package_boundary(
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
