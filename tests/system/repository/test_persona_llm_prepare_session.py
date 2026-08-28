from __future__ import annotations

import datetime as dt
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = next(
    parent
    for parent in Path(__file__).resolve().parents
    if (parent / "pyproject.toml").is_file()
)
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
    _, _, _, _, shell_session = prepare.import_runtime_types(REPO_ROOT)
    shell_root = tmp_path / "shell-sessions"
    monkeypatch.setattr(shell_session, "SHELL_DIR", shell_root)
    monkeypatch.setattr(shell_session, "_LOCKS_DIR", shell_root / ".locks")
    return shell_session


def test_import_runtime_types_uses_canonical_namespace_without_leaking_path() -> None:
    probe = f"""
import importlib.util
import json
import sys
from pathlib import Path

repo_root = Path({json.dumps(str(REPO_ROOT))})
script_path = Path({json.dumps(str(SCRIPT_PATH))})
source_root = (repo_root / "src").resolve()
legacy_plugins_root = source_root / "plugins"
legacy_package_root = source_root / "plugins" / "DicePP"

def is_path_entry(entry, target):
    try:
        return Path(entry).resolve() == target
    except (OSError, TypeError):
        return False

sys.path[:] = [
    entry
    for entry in sys.path
    if not is_path_entry(entry, source_root)
    and not is_path_entry(entry, legacy_plugins_root)
    and not is_path_entry(entry, legacy_package_root)
]
original_path = list(sys.path)

spec = importlib.util.spec_from_file_location("persona_llm_prepare_session_probe", script_path)
assert spec and spec.loader
prepare = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = prepare
spec.loader.exec_module(prepare)
bot_config, user_config, character_loader, persona_model, shell_session = prepare.import_runtime_types(repo_root)

print("__DICEPP_IMPORT_PROBE__" + json.dumps({{
    "path_restored": sys.path == original_path,
    "legacy_import_path_exposed": any(
        is_path_entry(entry, legacy_plugins_root)
        or is_path_entry(entry, legacy_package_root)
        for entry in sys.path
    ),
    "modules": [
        bot_config.__module__,
        character_loader.__module__,
        persona_model.__module__,
        shell_session.__name__,
    ],
}}))
"""
    completed = subprocess.run(
        [sys.executable, "-I", "-c", probe],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    result_line = next(
        line
        for line in completed.stdout.splitlines()
        if line.startswith("__DICEPP_IMPORT_PROBE__")
    )
    result = json.loads(result_line.removeprefix("__DICEPP_IMPORT_PROBE__"))
    assert result == {
        "path_restored": True,
        "legacy_import_path_exposed": False,
        "modules": [
            "plugins.DicePP.core.config.pydantic_models",
            "plugins.DicePP.module.persona.character.loader",
            "plugins.DicePP.core.persona.models",
            "plugins.DicePP.shell.session",
        ],
    }


@pytest.mark.parametrize(
    ("pollution", "expected_detail"),
    [
        ("sys.path.insert(0, str(legacy_plugins_root))", "sys.path="),
        (
            "sys.path.insert(0, str(legacy_package_root / 'core'))",
            "sys.path=",
        ),
        (
            "sys.path.insert(0, str(legacy_plugins_root))\n"
            "import DicePP\n"
            "sys.path.remove(str(legacy_plugins_root))",
            "sys.modules=DicePP",
        ),
        (
            "sys.path.insert(0, str(legacy_package_root))\n"
            "import core\n"
            "sys.path.remove(str(legacy_package_root))",
            "sys.modules=core",
        ),
    ],
)
def test_import_runtime_types_rejects_legacy_process_pollution(
    pollution: str,
    expected_detail: str,
) -> None:
    probe = f"""
import importlib.util
import json
import sys
from pathlib import Path

repo_root = Path({json.dumps(str(REPO_ROOT))})
script_path = Path({json.dumps(str(SCRIPT_PATH))})
source_root = (repo_root / "src").resolve()
legacy_plugins_root = source_root / "plugins"
legacy_package_root = legacy_plugins_root / "DicePP"

spec = importlib.util.spec_from_file_location("persona_llm_prepare_session_probe", script_path)
assert spec and spec.loader
prepare = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = prepare
spec.loader.exec_module(prepare)

{pollution}

try:
    prepare.import_runtime_types(repo_root)
except prepare.PreparationError as exc:
    print("__DICEPP_IMPORT_POLLUTION_PROBE__" + json.dumps({{"message": str(exc)}}))
else:
    raise AssertionError("legacy import pollution was accepted")
"""
    completed = subprocess.run(
        [sys.executable, "-I", "-c", probe],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    result_line = next(
        line
        for line in completed.stdout.splitlines()
        if line.startswith("__DICEPP_IMPORT_POLLUTION_PROBE__")
    )
    message = json.loads(
        result_line.removeprefix("__DICEPP_IMPORT_POLLUTION_PROBE__")
    )["message"]
    assert "旧 DicePP 导入身份" in message
    assert expected_detail in message


def test_import_runtime_types_allows_unrelated_plugin_paths_and_modules(
    tmp_path: Path,
) -> None:
    foreign_plugins_root = tmp_path / "unrelated" / "src" / "plugins"
    probe = f"""
import importlib.util
import json
import sys
from pathlib import Path

repo_root = Path({json.dumps(str(REPO_ROOT))})
script_path = Path({json.dumps(str(SCRIPT_PATH))})
foreign_plugins_root = Path({json.dumps(str(foreign_plugins_root))})
foreign_dicepp_root = foreign_plugins_root / "DicePP"
foreign_dicepp_root.mkdir(parents=True)
(foreign_dicepp_root / "__init__.py").write_text(
    "marker = 'external'\\n", encoding="utf-8"
)

spec = importlib.util.spec_from_file_location("persona_llm_prepare_session_probe", script_path)
assert spec and spec.loader
prepare = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = prepare
spec.loader.exec_module(prepare)

sys.path.insert(0, str(foreign_plugins_root))
import DicePP
assert Path(DicePP.__file__).resolve().is_relative_to(foreign_dicepp_root)

bot_config, user_config, character_loader, persona_model, shell_session = prepare.import_runtime_types(repo_root)
print("__DICEPP_UNRELATED_IMPORT_PROBE__" + json.dumps({{
    "external_package": str(Path(DicePP.__file__).resolve()),
    "modules": [
        bot_config.__module__,
        character_loader.__module__,
        persona_model.__module__,
        shell_session.__name__,
    ],
}}))
"""
    completed = subprocess.run(
        [sys.executable, "-I", "-c", probe],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    result_line = next(
        line
        for line in completed.stdout.splitlines()
        if line.startswith("__DICEPP_UNRELATED_IMPORT_PROBE__")
    )
    result = json.loads(
        result_line.removeprefix("__DICEPP_UNRELATED_IMPORT_PROBE__")
    )
    assert result == {
        "external_package": str(
            (foreign_plugins_root / "DicePP" / "__init__.py").resolve()
        ),
        "modules": [
            "plugins.DicePP.core.config.pydantic_models",
            "plugins.DicePP.module.persona.character.loader",
            "plugins.DicePP.core.persona.models",
            "plugins.DicePP.shell.session",
        ],
    }


def _prepared_for_confirmation(
    *estimates: prepare.AgentRunEstimate,
) -> prepare.PreparedSession:
    return prepare.PreparedSession(
        name="test-session",
        path=Path("test-session"),
        credential_path=Path("test_llm.local.json"),
        model="deepseek-v4-flash",
        scenarios=tuple(estimate.scenario for estimate in estimates),
        estimates=estimates,
        background_max_rounds=10,
        sa_max_rounds=100,
    )


def test_confirmation_summary_prioritizes_single_scenario_total() -> None:
    prepared = _prepared_for_confirmation(
        prepare.AgentRunEstimate(
            scenario="群聊跑团多人上下文",
            entries=(("Chat", 10), ("Scoring", 1), ("Unused", 0)),
        )
    )

    assert prepare.format_confirmation(prepared) == (
        "启动真实 LLM 测试前请确认：\n\n"
        "- 场景：群聊跑团多人上下文\n"
        "- Agent Run：共 11 次\n"
        "  - Chat：10 次\n"
        "  - Scoring：1 次\n\n"
        "确认开始？"
    )


def test_confirmation_summary_groups_scenarios_and_marks_upper_bound() -> None:
    prepared = _prepared_for_confirmation(
        prepare.AgentRunEstimate(
            scenario="一天连续 warp",
            entries=(("DM（上界）", 12), ("Diary", 1)),
            upper_bound=True,
        ),
        prepare.AgentRunEstimate(
            scenario="私聊跑团多轮",
            entries=(("Chat", 7), ("Scoring", 1)),
        ),
    )

    assert prepare.format_confirmation(prepared) == (
        "启动真实 LLM 测试前请确认：\n\n"
        "- 场景：一天连续 warp、私聊跑团多轮\n"
        "- Agent Run：预计最多 21 次\n"
        "  - 一天连续 warp：最多 13 次（DM 12、Diary 1）\n"
        "  - 私聊跑团多轮：8 次（Chat 7、Scoring 1）\n\n"
        "确认开始？"
    )


def test_prepare_session_writes_valid_workspace_without_exposing_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, skill = _make_test_repo(tmp_path)
    secret = "sk-test-never-print-this"
    _write_json(
        skill / "test_llm.local.json",
        {"deepseek_api_key": secret},
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
    assert result.model == "deepseek-v4-flash"
    assert not (result.path / "config" / "global.json").exists()

    assert (result.path / "config" / "user.json").exists()

    account_path = (
        result.path
        / "config"
        / "bots"
        / f"{shell_session.bot_id_for_session(result.name)}.json"
    )
    account = json.loads(account_path.read_text(encoding="utf-8"))
    assert "providers" not in account.get("persona_ai", {})
    assert account["persona_ai"]["character_path"] == str(
        (result.path / "content" / "characters").resolve()
    )
    assert account["persona_ai"]["character_name"] == prepare.CHARACTER_NAME
    assert (
        result.path
        / "content"
        / "characters"
        / prepare.CHARACTER_NAME
        / "character.yaml"
    ).is_file()

    from plugins.DicePP.core.config.loader import ConfigLoader

    loaded = ConfigLoader(
        data_path=str(result.path / "config"),
        account=shell_session.bot_id_for_session(result.name),
    ).load()
    assert loaded.persona_ai.character_name == prepare.CHARACTER_NAME
    assert loaded.persona_ai.character_path == str(
        (result.path / "content" / "characters").resolve()
    )
    assert loaded.persona_ai.enabled is True
    assert json.loads((result.path / "config" / "user.json").read_text())[
        "deepseek_api_key"
    ] == secret

    summary = prepare.format_summary(result)
    assert secret not in summary
    assert "尚未启动 Runtime" in summary
    assert "一天连续 warp" in summary
    assert "私聊跑团多轮" in summary
    assert "群聊跑团多人上下文" in summary
    assert "Chat: 7" in summary
    assert "Chat: 10" in summary
    assert summary.count("Scoring: 1") == 2


def test_prepare_rejects_drifted_override_and_unignored_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, skill = _make_test_repo(tmp_path)
    _write_json(
        skill / "test_llm.local.json",
        {"deepseek_api_key": "sk-test"},
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
        {"deepseek_api_key": "sk-test"},
    )
    with pytest.raises(prepare.PreparationError, match="未被 Git ignore"):
        prepare.prepare_session(
            repo_root=repo2,
            skill_dir=skill2,
            scenarios=("private",),
        )
