"""Docker build context contracts for locally generated Python artifacts."""

from pathlib import Path

import pytest
import yaml

from tests.support.paths import find_repository_root


ROOT = find_repository_root(Path(__file__))
DOCKERIGNORE = ROOT / ".dockerignore"


def _rules() -> set[str]:
    return {
        line.strip()
        for line in DOCKERIGNORE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }


@pytest.mark.quick
def test_docker_build_context_recursively_excludes_python_build_artifacts():
    """Stale metadata and bytecode must not override an image's installed package."""
    assert {
        "**/*.egg-info",
        "**/__pycache__",
        "**/*.py[cod]",
        "**/*$py.class",
    } <= _rules()


@pytest.mark.quick
def test_docker_build_context_excludes_manager_runtime_credentials():
    """Container-written Manager credentials/state must stay out of the build context.

    Containerized local runs write these as root with restrictive modes, which
    both leaks credentials into the context and breaks later host-side builds.
    """
    assert {"manager/control/", "manager/state/"} <= _rules()


def test_compose_describes_one_dashboard_and_bot_service():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))

    assert set(compose["services"]) == {"dicepp"}
    service = compose["services"]["dicepp"]
    assert service["build"]["dockerfile"] == "Dockerfile"
    assert service["ports"] == ["4090:4090"]
    assert service["expose"] == ["8080"]
    assert service["environment"]["DICEPP_ONEBOT_HOST"] == "0.0.0.0"
    assert "manager" not in compose["services"]
    assert "4091" not in str(service)
    assert "/var/run/docker.sock" not in str(service)
