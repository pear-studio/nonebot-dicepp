#!/usr/bin/env python3
"""Prepare an isolated DicePP Shell session for Persona real-LLM regression.

This script is deliberately offline: it validates and writes configuration,
but never starts a Runtime, constructs an LLM provider, or performs network I/O.
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import json
import secrets
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


CHARACTER_NAME = "persona-llm-test-dnd"
CHARACTER_DISPLAY_NAME = "艾琳娜·银枝"
TEST_GROUP_ID = "llm_test_group"
SCENARIO_ORDER = ("warp", "private", "group")
PRIVATE_CHAT_RUNS = 7
GROUP_CHAT_RUNS = 10
PRIVATE_INTERACTIONS_BY_USER = (7,)
GROUP_INTERACTIONS_BY_USER = (1, 2, 7)
LEGACY_RUNTIME_IMPORT_ROOTS = frozenset(
    {"core", "module", "utils", "adapter", "shell", "frozen", "DicePP"}
)


class PreparationError(RuntimeError):
    """Raised when the offline preparation contract is not satisfied."""


@dataclass(frozen=True)
class AgentRunEstimate:
    scenario: str
    entries: tuple[tuple[str, int], ...]
    notes: tuple[str, ...] = ()
    upper_bound: bool = False


@dataclass(frozen=True)
class PreparedSession:
    name: str
    path: Path
    credential_path: Path
    providers: tuple[str, ...]
    probe_models: tuple[str, ...]
    scenarios: tuple[str, ...]
    estimates: tuple[AgentRunEstimate, ...]
    background_max_rounds: int
    sa_max_rounds: int


def find_repo_root(script_path: Path | None = None) -> Path:
    start = (script_path or Path(__file__)).resolve()
    for parent in start.parents:
        if (parent / "pyproject.toml").is_file() and (
            parent / "src" / "plugins" / "DicePP"
        ).is_dir():
            return parent
    raise PreparationError("无法从脚本路径定位 DicePP 仓库根目录")


def load_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise PreparationError(f"{label}不存在: {path}") from exc
    except json.JSONDecodeError as exc:
        raise PreparationError(
            f"{label}不是合法 JSON: {path} ({exc.msg})"
        ) from exc
    except OSError as exc:
        raise PreparationError(f"无法读取{label}: {path} ({exc})") from exc
    if not isinstance(value, dict):
        raise PreparationError(f"{label}根节点必须是 JSON object: {path}")
    return value


def assert_git_ignored(repo_root: Path, path: Path) -> None:
    try:
        relative = path.resolve().relative_to(repo_root.resolve())
    except ValueError as exc:
        raise PreparationError(
            f"凭据文件必须位于当前仓库内: {path}"
        ) from exc
    completed = subprocess.run(
        ["git", "check-ignore", "--quiet", "--", relative.as_posix()],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise PreparationError(
            f"凭据文件未被 Git ignore，拒绝继续: {path}"
        )


def validate_override_paths(
    base: Mapping[str, Any],
    override: Mapping[str, Any],
    *,
    prefix: tuple[str, ...] = (),
) -> None:
    """Require every override path to exist in the runtime default model."""
    for key, value in override.items():
        path = prefix + (key,)
        dotted = ".".join(path)
        if key not in base:
            raise PreparationError(
                f"测试覆盖字段已漂移，BotConfig 默认配置中不存在: {dotted}"
            )
        base_value = base[key]
        if isinstance(value, dict):
            if not isinstance(base_value, dict):
                raise PreparationError(
                    f"测试覆盖字段类型已漂移，不再是 object: {dotted}"
                )
            validate_override_paths(base_value, value, prefix=path)


def validate_local_credentials(
    local_config: Mapping[str, Any],
    default_config: Mapping[str, Any],
) -> dict[str, str]:
    if set(local_config) != {"persona_ai"}:
        raise PreparationError(
            "test_llm.local.json 只能包含 persona_ai.providers API key"
        )
    persona = local_config.get("persona_ai")
    if not isinstance(persona, dict) or set(persona) != {"providers"}:
        raise PreparationError(
            "test_llm.local.json 的 persona_ai 只能包含 providers"
        )
    providers = persona.get("providers")
    if not isinstance(providers, dict) or not providers:
        raise PreparationError(
            "test_llm.local.json 至少要为一个正式 provider 配置 api_key"
        )

    default_persona = default_config.get("persona_ai")
    builtin_providers = (
        default_persona.get("providers")
        if isinstance(default_persona, dict)
        else None
    )
    if not isinstance(builtin_providers, dict):
        raise PreparationError(
            "BotConfig 默认配置缺少 persona_ai.providers"
        )

    result: dict[str, str] = {}
    for provider_name, provider_override in providers.items():
        if provider_name not in builtin_providers:
            raise PreparationError(
                f"不允许自定义 provider: {provider_name}"
            )
        if (
            not isinstance(provider_override, dict)
            or set(provider_override) != {"api_key"}
        ):
            raise PreparationError(
                f"provider {provider_name!r} 只能包含 api_key"
            )
        api_key = provider_override.get("api_key")
        if not isinstance(api_key, str) or not api_key.strip():
            raise PreparationError(
                f"provider {provider_name!r} 的 api_key 不能为空"
            )
        if "api_key" not in builtin_providers[provider_name]:
            raise PreparationError(
                f"正式 provider {provider_name!r} 缺少 api_key 字段"
            )
        result[provider_name] = api_key.strip()
    return result


def deep_merge(
    base: Mapping[str, Any],
    override: Mapping[str, Any],
) -> dict[str, Any]:
    result = copy.deepcopy(dict(base))
    for key, value in override.items():
        if (
            key in result
            and isinstance(result[key], dict)
            and isinstance(value, dict)
        ):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def import_runtime_types(repo_root: Path):
    source_root = (repo_root / "src").resolve()
    _assert_runtime_import_environment_is_clean(source_root)

    src_root = str(source_root)
    added_src_root = not any(
        _resolve_path_entry(entry) == source_root for entry in sys.path
    )
    if added_src_root:
        sys.path.insert(0, src_root)

    try:
        from plugins.DicePP.core.config.pydantic_models import BotConfig
        from plugins.DicePP.core.persona.models import PersonaModel
        from plugins.DicePP.module.persona.character.loader import CharacterLoader
        from plugins.DicePP.shell import session as shell_session

        return BotConfig, CharacterLoader, PersonaModel, shell_session
    finally:
        if added_src_root:
            sys.path.remove(src_root)


def _assert_runtime_import_environment_is_clean(source_root: Path) -> None:
    plugins_root = source_root / "plugins"
    dicepp_root = plugins_root / "DicePP"
    legacy_paths = [
        path
        for entry in sys.path
        if (path := _resolve_path_entry(entry)) is not None
        and _is_legacy_runtime_import_path(
            path,
            source_root=source_root,
            plugins_root=plugins_root,
            dicepp_root=dicepp_root,
        )
    ]
    legacy_modules = _loaded_legacy_runtime_modules(dicepp_root)
    if not legacy_paths and not legacy_modules:
        return

    details: list[str] = []
    if legacy_paths:
        details.append(
            "sys.path=" + ", ".join(str(path) for path in legacy_paths)
        )
    if legacy_modules:
        details.append("sys.modules=" + ", ".join(legacy_modules))
    raise PreparationError(
        "检测到旧 DicePP 导入身份，拒绝在可能产生模块双身份的进程中加载运行时类型: "
        + "; ".join(details)
        + "。请移除当前仓库的 src/plugins 或 src/plugins/DicePP 路径，并在干净 Python 进程中重试。"
    )


def _resolve_path_entry(entry: object) -> Path | None:
    try:
        return Path(entry).resolve()
    except (OSError, TypeError, ValueError):
        return None


def _is_legacy_runtime_import_path(
    path: Path,
    *,
    source_root: Path,
    plugins_root: Path,
    dicepp_root: Path,
) -> bool:
    """Return whether *path* can expose this repository's DicePP twice."""
    if path == source_root:
        return False
    return (
        path == plugins_root
        or path == dicepp_root
        or dicepp_root in path.parents
    )


def _loaded_legacy_runtime_modules(dicepp_root: Path) -> tuple[str, ...]:
    loaded: list[str] = []
    for module_name, module in tuple(sys.modules.items()):
        if module is None:
            continue
        root_name = module_name.split(".", 1)[0]
        if root_name not in LEGACY_RUNTIME_IMPORT_ROOTS:
            continue
        if _module_originates_under(module, dicepp_root):
            loaded.append(module_name)
    return tuple(sorted(loaded))


def _module_originates_under(module: object, dicepp_root: Path) -> bool:
    locations = [getattr(module, "__file__", None)]
    locations.extend(getattr(module, "__path__", ()) or ())
    for location in locations:
        path = _resolve_path_entry(location)
        if path == dicepp_root or (path is not None and dicepp_root in path.parents):
            return True
    return False


def validate_character_assets(
    asset_root: Path,
    character_loader_type: Any,
    persona_model_type: Any,
) -> Any:
    expected_dir = asset_root / CHARACTER_NAME
    for filename in ("character.yaml", "skin.yaml"):
        if not (expected_dir / filename).is_file():
            raise PreparationError(
                f"测试角色卡缺少 {filename}: {expected_dir}"
            )

    character = character_loader_type(str(asset_root)).load(CHARACTER_NAME)
    if character is None:
        raise PreparationError("测试 character.yaml 无法通过正式 CharacterLoader")
    try:
        import yaml

        skin_data = yaml.safe_load(
            (expected_dir / "skin.yaml").read_text(encoding="utf-8")
        )
        if not isinstance(skin_data, dict):
            raise TypeError("skin.yaml root is not an object")
        skin_data["name"] = CHARACTER_NAME
        persona_model_type.model_validate(skin_data)
    except Exception as exc:
        raise PreparationError(
            f"测试 skin.yaml 无法通过正式 PersonaModel 校验: {exc}"
        ) from exc
    return character


def build_session_user_config(
    default_config: Mapping[str, Any],
    test_overrides: Mapping[str, Any],
    credentials: Mapping[str, str],
    character_path: Path,
) -> dict[str, Any]:
    builtin_providers = default_config["persona_ai"]["providers"]
    provider_overrides: dict[str, dict[str, Any]] = {}
    for provider_name, provider_config in builtin_providers.items():
        has_key = provider_name in credentials
        provider_overrides[provider_name] = {
            "enabled": bool(
                has_key and provider_config.get("enabled", True)
            )
        }
        if has_key:
            provider_overrides[provider_name]["api_key"] = credentials[
                provider_name
            ]

    return deep_merge(
        test_overrides,
        {
            "persona_ai": {
                "character_path": str(character_path.resolve()),
                "providers": provider_overrides,
            }
        },
    )


def validate_merged_config(
    bot_config_type: Any,
    default_config: Mapping[str, Any],
    user_config: Mapping[str, Any],
    account_config: Mapping[str, Any],
) -> Any:
    merged = deep_merge(default_config, user_config)
    merged = deep_merge(merged, account_config)
    try:
        return bot_config_type.model_validate(merged)
    except Exception as exc:
        details = "配置字段不合法"
        if hasattr(exc, "errors"):
            errors = exc.errors(include_input=False, include_url=False)
            details = "; ".join(
                f"{'.'.join(str(part) for part in item.get('loc', ()))}: "
                f"{item.get('msg', item.get('type', 'validation error'))}"
                for item in errors
            )
        raise PreparationError(
            f"合并后的 session 配置未通过 BotConfig 校验: {details}"
        ) from exc


def build_session_name(
    now: dt.datetime | None = None,
    token: str | None = None,
) -> str:
    current = now or dt.datetime.now()
    suffix = token or secrets.token_hex(4)
    return f"llm-test-{current:%y%m%d-%H%M%S}-{suffix}"


def estimate_agent_runs(
    scenarios: Sequence[str],
    config: Any,
    character: Any,
) -> tuple[AgentRunEstimate, ...]:
    scoring_interval = config.persona_ai.scoring_interval

    def scoring_runs(interactions_by_user: Sequence[int]) -> int:
        return sum(count // scoring_interval for count in interactions_by_user)

    results: list[AgentRunEstimate] = []
    for scenario in scenarios:
        if scenario == "warp":
            calendar_days_max = 2
            daily_events = character.extensions.daily_events_count
            life_slots = calendar_days_max * (daily_events + 2)
            persona = config.persona_ai
            chain_depth = persona.character_life_chain_max_depth
            proactive_labels = (
                int(persona.proactive_share_schedule_morning_enabled)
                + len(persona.proactive_share_schedule_times)
                + int(persona.proactive_share_schedule_evening_enabled)
            )
            force_targets = len(
                set(persona.proactive_always_send_groups)
            ) + len(set(persona.proactive_always_send_users))
            results.append(
                AgentRunEstimate(
                    scenario="一天连续 warp",
                    entries=(
                        ("DM（上界）", life_slots * chain_depth),
                        ("Character 反应（上界）", life_slots * chain_depth),
                        ("Diary", 1),
                        ("SA", 1),
                        (
                            "Proactive Chat（上界）",
                            calendar_days_max
                            * proactive_labels
                            * force_targets,
                        ),
                    ),
                    notes=(
                        "离线估算按 24 小时窗口最多触及 2 个日历日；"
                        "serve 后以 warp --dry-run 为准。",
                    ),
                    upper_bound=True,
                )
            )
        elif scenario == "private":
            results.append(
                AgentRunEstimate(
                    scenario="私聊跑团多轮",
                    entries=(
                        ("Chat", PRIVATE_CHAT_RUNS),
                        (
                            "Scoring",
                            scoring_runs(PRIVATE_INTERACTIONS_BY_USER),
                        ),
                    ),
                    notes=(
                        "包含一次真实 roll_dice 验收、warp 事件查询和私聊秘密写入。",
                    ),
                )
            )
        elif scenario == "group":
            results.append(
                AgentRunEstimate(
                    scenario="群聊跑团多人上下文",
                    entries=(
                        ("Chat", GROUP_CHAT_RUNS),
                        (
                            "Scoring",
                            scoring_runs(GROUP_INTERACTIONS_BY_USER),
                        ),
                    ),
                    notes=(
                        "三条普通群消息只进入上下文，不触发 Persona 回复。",
                        "包含多人归属、事实覆盖、roll_dice、角色书、私聊泄漏与"
                        "三人连续 .jrrp 验收。",
                    ),
                )
            )
        else:
            raise PreparationError(f"未知场景: {scenario}")
    return tuple(results)


def configured_probe_models(
    default_config: Mapping[str, Any],
    credentials: Mapping[str, str],
) -> tuple[str, ...]:
    models: list[str] = []
    for provider_name in credentials:
        provider = default_config["persona_ai"]["providers"][provider_name]
        if not provider.get("enabled", True):
            continue
        for model in provider.get("models", []):
            if model.get("enabled", True):
                models.append(f"{provider_name}/{model['name']}")
    if not models:
        raise PreparationError(
            "提供了 API key，但正式配置中没有对应的已启用模型"
        )
    return tuple(models)


def normalize_scenarios(values: Iterable[str]) -> tuple[str, ...]:
    selected = set(values)
    unknown = selected.difference(SCENARIO_ORDER)
    if unknown:
        raise PreparationError(
            f"未知场景: {', '.join(sorted(unknown))}"
        )
    if not selected:
        raise PreparationError("至少选择一个场景")
    return tuple(value for value in SCENARIO_ORDER if value in selected)


def write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def prepare_session(
    *,
    repo_root: Path,
    skill_dir: Path,
    scenarios: Sequence[str],
    now: dt.datetime | None = None,
    token: str | None = None,
) -> PreparedSession:
    repo_root = repo_root.resolve()
    skill_dir = skill_dir.resolve()
    selected = normalize_scenarios(scenarios)
    credential_path = skill_dir / "test_llm.local.json"
    assert_git_ignored(repo_root, credential_path)

    test_overrides = load_json_object(
        skill_dir / "assets" / "test-overrides.json",
        "测试覆盖配置",
    )
    local_config = load_json_object(credential_path, "本地 LLM 凭据")

    (
        bot_config_type,
        character_loader_type,
        persona_model_type,
        shell_session,
    ) = import_runtime_types(repo_root)
    default_config = bot_config_type().model_dump(mode="json", by_alias=True)
    validate_override_paths(default_config, test_overrides)
    credentials = validate_local_credentials(local_config, default_config)
    probe_models = configured_probe_models(default_config, credentials)
    character_asset_root = skill_dir / "assets"
    character = validate_character_assets(
        character_asset_root,
        character_loader_type,
        persona_model_type,
    )

    name = build_session_name(now=now, token=token)
    session_path = shell_session.get_session_dir(name)
    character_path = session_path / "content" / "characters"
    user_config = build_session_user_config(
        default_config,
        test_overrides,
        credentials,
        character_path,
    )
    account_config = {
        "persona": CHARACTER_NAME,
        "nickname": CHARACTER_DISPLAY_NAME,
    }
    validated = validate_merged_config(
        bot_config_type,
        default_config,
        user_config,
        account_config,
    )

    if session_path.exists():
        raise PreparationError(f"生成的 session 已存在，请重试: {name}")

    try:
        created_path = shell_session.create_session(
            name,
            group_id=TEST_GROUP_ID,
        )
        write_json(created_path / "config" / "user.json", user_config)
        write_json(
            created_path
            / "config"
            / "bots"
            / f"{shell_session.bot_id_for_session(name)}.json",
            account_config,
        )
        shutil.copytree(
            character_asset_root / CHARACTER_NAME,
            created_path / "content" / "characters" / CHARACTER_NAME,
        )
    except Exception:
        if session_path.exists():
            try:
                shell_session.delete_session(name)
            except Exception:
                pass
        raise

    estimates = estimate_agent_runs(selected, validated, character)
    return PreparedSession(
        name=name,
        path=created_path,
        credential_path=credential_path,
        providers=tuple(credentials),
        probe_models=probe_models,
        scenarios=selected,
        estimates=estimates,
        background_max_rounds=validated.persona_ai.background_llm_max_rounds,
        sa_max_rounds=validated.persona_ai.sa_max_rounds,
    )


def format_summary(prepared: PreparedSession) -> str:
    lines = [
        "Persona 真实 LLM 回归 session 已离线准备完成。",
        f"Session: {prepared.name}",
        f"目录: {prepared.path}",
        f"凭据文件: {prepared.credential_path}",
        f"已配置 provider: {', '.join(prepared.providers)}",
        f"Runtime 启动将 probe {len(prepared.probe_models)} 个模型:",
    ]
    lines.extend(f"  - {model}" for model in prepared.probe_models)
    lines.append("Agent Run 估算:")
    for estimate in prepared.estimates:
        lines.append(f"  {estimate.scenario}:")
        lines.extend(
            f"    - {label}: {count}"
            for label, count in estimate.entries
        )
        lines.extend(f"    - 说明: {note}" for note in estimate.notes)
    lines.extend(
        (
            "正式轮次上限:",
            f"  - background_llm_max_rounds: "
            f"{prepared.background_max_rounds}",
            f"  - sa_max_rounds: {prepared.sa_max_rounds}",
            "尚未启动 Runtime，未执行模型 probe，也未发出任何 LLM 请求。",
            "",
            format_confirmation(prepared),
        )
    )
    return "\n".join(lines)


def format_confirmation(prepared: PreparedSession) -> str:
    """生成 serve 前给用户确认的最小 Agent Run 摘要。"""

    estimates = prepared.estimates
    total = sum(
        count
        for estimate in estimates
        for _, count in estimate.entries
        if count > 0
    )
    has_upper_bound = any(estimate.upper_bound for estimate in estimates)
    total_label = "预计最多" if has_upper_bound else "共"
    lines = [
        "启动真实 LLM 测试前请确认：",
        "",
        f"- 场景：{'、'.join(estimate.scenario for estimate in estimates)}",
        f"- Agent Run：{total_label} {total} 次",
    ]

    if len(estimates) == 1:
        lines.extend(
            f"  - {label.replace('（上界）', '')}：{count} 次"
            for label, count in estimates[0].entries
            if count > 0
        )
    else:
        for estimate in estimates:
            entries = [
                (label.replace("（上界）", ""), count)
                for label, count in estimate.entries
                if count > 0
            ]
            subtotal = sum(count for _, count in entries)
            subtotal_label = f"最多 {subtotal}" if estimate.upper_bound else str(subtotal)
            distribution = "、".join(
                f"{label} {count}" for label, count in entries
            )
            lines.append(
                f"  - {estimate.scenario}：{subtotal_label} 次（{distribution}）"
            )

    lines.extend(("", "确认开始？"))
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="离线准备 Persona 真实 LLM 回归 session",
    )
    parser.add_argument(
        "--scenarios",
        nargs="+",
        required=True,
        choices=SCENARIO_ORDER,
        help="要准备的固定场景",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        repo_root = find_repo_root()
        skill_dir = Path(__file__).resolve().parent.parent
        prepared = prepare_session(
            repo_root=repo_root,
            skill_dir=skill_dir,
            scenarios=args.scenarios,
        )
    except PreparationError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(format_summary(prepared))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
