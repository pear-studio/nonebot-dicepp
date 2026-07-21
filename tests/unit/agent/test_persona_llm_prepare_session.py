from __future__ import annotations

import datetime as dt
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = (
    REPO_ROOT
    / "docs"
    / "agent"
    / "skills-dev"
    / "persona-llm-test"
    / "scripts"
    / "prepare_session.py"
)
SPEC = importlib.util.spec_from_file_location(
    "persona_llm_prepare_session",
    SCRIPT_PATH,
)
assert SPEC and SPEC.loader
prepare = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = prepare
SPEC.loader.exec_module(prepare)


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _make_test_repo(tmp_path: Path, *, ignored: bool = True) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    skill = (
        repo
        / "docs"
        / "agent"
        / "skills-dev"
        / "persona-llm-test"
    )
    (repo / "config").mkdir(parents=True)
    source_skill = SCRIPT_PATH.parent.parent
    _write_json(
        repo / "config" / "global.json",
        json.loads((REPO_ROOT / "config" / "global.json").read_text(encoding="utf-8")),
    )
    (skill / "assets").mkdir(parents=True)
    _write_json(
        skill / "assets" / "test-overrides.json",
        json.loads(
            (source_skill / "assets" / "test-overrides.json").read_text(
                encoding="utf-8"
            )
        ),
    )
    source_character = (
        source_skill / "assets" / prepare.CHARACTER_NAME
    )
    target_character = skill / "assets" / prepare.CHARACTER_NAME
    target_character.mkdir(parents=True)
    for filename in ("character.yaml", "skin.yaml"):
        (target_character / filename).write_text(
            (source_character / filename).read_text(encoding="utf-8"),
            encoding="utf-8",
        )
    if ignored:
        (skill / ".gitignore").write_text(
            "test_llm.local.json\n",
            encoding="utf-8",
        )
    subprocess.run(
        ["git", "init", "--quiet"],
        cwd=repo,
        check=True,
    )
    return repo, skill


def _isolate_shell_sessions(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    _, _, _, shell_session = prepare.import_runtime_types(REPO_ROOT)
    shell_root = tmp_path / "shell-sessions"
    monkeypatch.setattr(shell_session, "SHELL_DIR", shell_root)
    monkeypatch.setattr(shell_session, "_LOCKS_DIR", shell_root / ".locks")
    return shell_session


def test_prepare_session_writes_valid_workspace_without_exposing_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, skill = _make_test_repo(tmp_path)
    secret = "sk-test-never-print-this"
    _write_json(
        skill / "test_llm.local.json",
        {
            "persona_ai": {
                "providers": {
                    "deepseek": {"api_key": secret},
                }
            }
        },
    )
    shell_session = _isolate_shell_sessions(monkeypatch, tmp_path)

    result = prepare.prepare_session(
        repo_root=repo,
        skill_dir=skill,
        scenarios=("group", "warp", "private"),
        now=dt.datetime(2026, 7, 16, 20, 30, 45),
        token="abcd",
    )

    assert result.name == "llm-test-260716-203045-abcd"
    assert result.scenarios == ("warp", "private", "group")
    assert result.providers == ("deepseek",)
    assert all(model.startswith("deepseek/") for model in result.probe_models)

    user_config = json.loads(
        (result.path / "config" / "user.json").read_text(encoding="utf-8")
    )
    providers = user_config["persona_ai"]["providers"]
    assert providers["deepseek"] == {"enabled": True, "api_key": secret}
    assert providers["minimax"]["enabled"] is False
    assert providers["minimax_image"]["enabled"] is False
    assert providers["mimo"]["enabled"] is False
    assert user_config["persona_ai"]["character_path"] == str(
        (result.path / "content" / "characters").resolve()
    )

    account = json.loads(
        (
            result.path
            / "config"
            / "bots"
            / f"{shell_session.bot_id_for_session(result.name)}.json"
        ).read_text(encoding="utf-8")
    )
    assert account == {
        "persona": prepare.CHARACTER_NAME,
        "nickname": prepare.CHARACTER_DISPLAY_NAME,
    }
    assert (
        result.path
        / "content"
        / "characters"
        / prepare.CHARACTER_NAME
        / "character.yaml"
    ).is_file()

    from core.config.loader import ConfigLoader

    loaded = ConfigLoader(
        data_path=str(result.path / "config"),
        account=shell_session.bot_id_for_session(result.name),
    ).load()
    assert loaded.persona == prepare.CHARACTER_NAME
    assert loaded.persona_ai.character_path == str(
        (result.path / "content" / "characters").resolve()
    )
    assert loaded.persona_ai.providers["deepseek"].api_key == secret
    assert loaded.persona_ai.providers["deepseek"].enabled is True
    assert loaded.persona_ai.providers["minimax"].enabled is False

    summary = prepare.format_summary(result)
    assert secret not in summary
    assert "尚未启动 Runtime" in summary
    assert "一天连续 warp" in summary
    assert "私聊跑团多轮" in summary
    assert "群聊跑团多人上下文" in summary
    assert "Chat: 7" in summary
    assert summary.count("Scoring: 1") == 2


def test_prepare_rejects_custom_provider_before_creating_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, skill = _make_test_repo(tmp_path)
    _write_json(
        skill / "test_llm.local.json",
        {
            "persona_ai": {
                "providers": {
                    "custom-provider": {"api_key": "sk-test"},
                }
            }
        },
    )
    shell_session = _isolate_shell_sessions(monkeypatch, tmp_path)

    with pytest.raises(prepare.PreparationError, match="不允许自定义 provider"):
        prepare.prepare_session(
            repo_root=repo,
            skill_dir=skill,
            scenarios=("private",),
        )

    assert not shell_session.SHELL_DIR.exists()


def test_prepare_rejects_drifted_override_and_unignored_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, skill = _make_test_repo(tmp_path)
    _write_json(
        skill / "test_llm.local.json",
        {
            "persona_ai": {
                "providers": {
                    "deepseek": {"api_key": "sk-test"},
                }
            }
        },
    )
    _write_json(
        skill / "assets" / "test-overrides.json",
        {"persona_ai": {"removed_schedule_field": True}},
    )
    shell_session = _isolate_shell_sessions(monkeypatch, tmp_path)

    with pytest.raises(
        prepare.PreparationError,
        match="测试覆盖字段已漂移",
    ):
        prepare.prepare_session(
            repo_root=repo,
            skill_dir=skill,
            scenarios=("warp",),
        )
    assert not shell_session.SHELL_DIR.exists()

    repo2, skill2 = _make_test_repo(tmp_path / "unignored", ignored=False)
    _write_json(
        skill2 / "test_llm.local.json",
        {
            "persona_ai": {
                "providers": {
                    "deepseek": {"api_key": "sk-test"},
                }
            }
        },
    )
    with pytest.raises(prepare.PreparationError, match="未被 Git ignore"):
        prepare.prepare_session(
            repo_root=repo2,
            skill_dir=skill2,
            scenarios=("private",),
        )


def test_config_validation_error_never_includes_api_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, skill = _make_test_repo(tmp_path)
    secret = "sk-sensitive-validation-test"
    _write_json(
        skill / "test_llm.local.json",
        {
            "persona_ai": {
                "providers": {
                    "deepseek": {"api_key": secret},
                }
            }
        },
    )
    global_config = json.loads(
        (repo / "config" / "global.json").read_text(encoding="utf-8")
    )
    global_config["persona_ai"]["segment_soft_limit"] = 999
    global_config["persona_ai"]["segment_hard_limit"] = 1
    _write_json(repo / "config" / "global.json", global_config)
    shell_session = _isolate_shell_sessions(monkeypatch, tmp_path)

    with pytest.raises(prepare.PreparationError) as exc_info:
        prepare.prepare_session(
            repo_root=repo,
            skill_dir=skill,
            scenarios=("private",),
        )

    assert secret not in str(exc_info.value)
    assert "segment_soft_limit" in str(exc_info.value)
    assert not shell_session.SHELL_DIR.exists()
