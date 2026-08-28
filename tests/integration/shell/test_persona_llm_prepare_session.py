from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


REPO_ROOT = next(
    parent
    for parent in Path(__file__).resolve().parents
    if (parent / "pyproject.toml").is_file()
)
SCRIPT_PATH = REPO_ROOT / "docs" / "agent" / "skills-dev" / "persona-llm-test" / "scripts" / "prepare_session.py"
SPEC = importlib.util.spec_from_file_location("persona_llm_prepare_session_integration", SCRIPT_PATH)
assert SPEC and SPEC.loader
prepare = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = prepare
SPEC.loader.exec_module(prepare)


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _make_test_repo(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    skill = repo / "docs" / "agent" / "skills-dev" / "persona-llm-test"
    (repo / "config").mkdir(parents=True)
    source_skill = SCRIPT_PATH.parent.parent
    (skill / "assets").mkdir(parents=True)
    _write_json(skill / "assets" / "test-overrides.json", json.loads((source_skill / "assets" / "test-overrides.json").read_text(encoding="utf-8")))
    source_character = source_skill / "assets" / prepare.CHARACTER_NAME
    target_character = skill / "assets" / prepare.CHARACTER_NAME
    target_character.mkdir(parents=True)
    for filename in ("character.yaml", "skin.yaml"):
        (target_character / filename).write_text((source_character / filename).read_text(encoding="utf-8"), encoding="utf-8")
    return repo, skill


def _isolate_shell_sessions(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    _, _, _, _, shell_session = prepare.import_runtime_types(REPO_ROOT)
    shell_root = tmp_path / "shell-sessions"
    monkeypatch.setattr(shell_session, "SHELL_DIR", shell_root)
    monkeypatch.setattr(shell_session, "_LOCKS_DIR", shell_root / ".locks")
    return shell_session


def test_prepare_rejects_custom_provider_before_creating_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo, skill = _make_test_repo(tmp_path)
    _write_json(skill / "test_llm.local.json", {"custom_provider_api_key": "sk-test"})
    monkeypatch.setattr(prepare, "assert_git_ignored", lambda *_: None)
    shell_session = _isolate_shell_sessions(monkeypatch, tmp_path)

    with pytest.raises(prepare.PreparationError, match="只能包含 deepseek_api_key"):
        prepare.prepare_session(repo_root=repo, skill_dir=skill, scenarios=("private",))

    assert not shell_session.SHELL_DIR.exists()


def test_config_validation_error_never_includes_api_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo, skill = _make_test_repo(tmp_path)
    secret = "sk-sensitive-validation-test"
    _write_json(skill / "test_llm.local.json", {"deepseek_api_key": secret})
    test_overrides = json.loads(
        (skill / "assets" / "test-overrides.json").read_text(encoding="utf-8")
    )
    test_overrides["persona_ai"]["segment_soft_limit"] = 999
    test_overrides["persona_ai"]["segment_hard_limit"] = 1
    _write_json(skill / "assets" / "test-overrides.json", test_overrides)
    monkeypatch.setattr(prepare, "assert_git_ignored", lambda *_: None)
    shell_session = _isolate_shell_sessions(monkeypatch, tmp_path)

    with pytest.raises(prepare.PreparationError) as exc_info:
        prepare.prepare_session(repo_root=repo, skill_dir=skill, scenarios=("private",))

    assert secret not in str(exc_info.value)
    assert "segment_soft_limit" in str(exc_info.value)
    assert not shell_session.SHELL_DIR.exists()
