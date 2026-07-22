"""Suite-wide safety hooks and lightweight fixtures."""

from __future__ import annotations

import atexit
import os
from pathlib import Path
import shutil
import sys
import tempfile

import pytest

from tests.support.fs_utils import rmtree_retry
from tests.support.paths import find_repository_root
from tests.support.pollution import (
    assert_repository_unchanged,
    capture_repository_snapshot,
)
from tests.support.xdist import (
    calculate_xdist_worker_count,
    detect_available_cpu_count,
)


@pytest.hookimpl(tryfirst=True)
def pytest_xdist_auto_num_workers(config: pytest.Config) -> int:
    """Keep default parallelism bounded while honoring xdist's env override."""
    del config
    return calculate_xdist_worker_count(
        detect_available_cpu_count(),
        os.getenv("PYTEST_XDIST_AUTO_NUM_WORKERS"),
    )


# Every worker gets an isolated application root before test modules import
# DicePP. This is a suite safety boundary, not a unit-test filesystem fixture.
_PYTEST_WORKER_ID = os.getenv("PYTEST_XDIST_WORKER", "main")
_TEST_APP_DIR = tempfile.mkdtemp(prefix=f"dicepp-test-{_PYTEST_WORKER_ID}-")
os.environ["DICEPP_APP_DIR"] = _TEST_APP_DIR
os.environ["DICEPP_PROJECT_ROOT"] = _TEST_APP_DIR

_REAL_PROJECT = find_repository_root(Path(__file__))
_TEST_PROJECT = Path(_TEST_APP_DIR)
for relative_path in (Path("config/bots/_template.json"), Path("config/global.json")):
    source = _REAL_PROJECT / relative_path
    if not source.exists():
        raise RuntimeError(f"测试模板不存在: {source}")
    target = _TEST_PROJECT / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(source, target)

_DICEPP_PATH = _REAL_PROJECT / "src" / "plugins" / "DicePP"
if str(_DICEPP_PATH) not in sys.path:
    sys.path.insert(0, str(_DICEPP_PATH))

# Production imports must happen only after the isolated app root is active.
from tests.support.persona_llm import MockCoordinator


_REAL_REPOSITORY_BASELINE = capture_repository_snapshot(_REAL_PROJECT)


def _cleanup_test_app_dir() -> None:
    try:
        from loguru import logger as loguru_logger

        loguru_logger.remove()
    except (ImportError, ValueError):
        pass
    rmtree_retry(_TEST_APP_DIR)


atexit.register(_cleanup_test_app_dir)


def _assert_no_real_repo_pollution() -> None:
    assert_repository_unchanged(_REAL_PROJECT, _REAL_REPOSITORY_BASELINE)


@pytest.fixture(scope="session", autouse=True)
def _test_session_cleanup_and_pollution_check():
    yield
    _assert_no_real_repo_pollution()
    _cleanup_test_app_dir()


@pytest.fixture
def mock_coordinator():
    """Create an in-memory Persona LLM call coordinator."""
    return MockCoordinator()
