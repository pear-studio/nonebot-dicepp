#!/usr/bin/env python3
"""Synchronize DicePP agent rules and skills into local tool directories."""

from __future__ import annotations

import argparse
import copy
import fnmatch
import json
import os
import platform
import shutil
import stat
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


TARGETS = ("codex", "claude")
STATE_FILE = ".agent-sync.json"
ENV_FILE = ".agent-env.json"


class SyncError(RuntimeError):
    pass


@dataclass(frozen=True)
class TargetSpec:
    name: str
    root: Path
    skills_dir: Path
    rule_files: tuple[Path, ...]


@dataclass(frozen=True)
class SkillSource:
    name: str
    root: Path
    path: Path


@dataclass
class Config:
    env: str
    link_mode: str
    ignore_skills: list[str]
    raw: dict[str, Any]


def agent_dir() -> Path:
    return Path(__file__).resolve().parent


def repo_root() -> Path:
    return agent_dir().parent.parent


def rel(path: Path) -> str:
    absolute = path if path.is_absolute() else (Path.cwd() / path)
    try:
        return absolute.absolute().relative_to(repo_root().absolute()).as_posix()
    except ValueError:
        return str(path)


def load_json(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as exc:
        raise SyncError(f"Invalid JSON in {rel(path)}: {exc}") from exc
    if not isinstance(data, dict):
        raise SyncError(f"{rel(path)} must contain a JSON object.")
    return data


def resolve_config(env_arg: str | None, target: str | None = None) -> Config:
    raw = load_json(agent_dir() / ENV_FILE)
    env = env_arg or raw.get("env")
    if not isinstance(env, str) or not env.strip():
        raise SyncError(
            f"Agent environment is not set. Create {rel(agent_dir() / ENV_FILE)} "
            'with {"env": "dev"} or pass --env dev.'
        )
    env = env.strip()

    link_mode = raw.get("linkMode", "auto")
    if link_mode not in {"auto", "symlink", "junction", "copy"}:
        raise SyncError("linkMode must be one of: auto, symlink, junction, copy.")

    ignore = raw.get("ignore", {})
    if ignore is None:
        ignore = {}
    if not isinstance(ignore, dict):
        raise SyncError("ignore must be an object.")

    patterns: list[str] = []
    skills = ignore.get("skills", [])
    if isinstance(skills, list):
        patterns.extend(str(item) for item in skills)
    elif skills:
        raise SyncError("ignore.skills must be a list.")

    target_ignores = ignore.get("targets", {})
    if target and isinstance(target_ignores, dict):
        target_block = target_ignores.get(target, {})
        if isinstance(target_block, dict):
            target_skills = target_block.get("skills", [])
            if isinstance(target_skills, list):
                patterns.extend(str(item) for item in target_skills)
            elif target_skills:
                raise SyncError(f"ignore.targets.{target}.skills must be a list.")

    return Config(env=env, link_mode=link_mode, ignore_skills=patterns, raw=raw)


def validate_env(env: str) -> None:
    missing: list[str] = []
    if not (agent_dir() / "rules" / "common.md").is_file():
        missing.append("rules/common.md")
    if not (agent_dir() / "rules" / f"{env}.md").is_file():
        missing.append(f"rules/{env}.md")
    if not (agent_dir() / f"skills-{env}").exists():
        missing.append(f"skills-{env}/")
    if missing:
        joined = ", ".join(missing)
        raise SyncError(f"Unknown or incomplete agent env {env!r}; missing {joined}.")


def target_spec(name: str) -> TargetSpec:
    root = repo_root()
    if name == "codex":
        base = root / ".codex"
        return TargetSpec(name, base, base / "skills", (base / "AGENTS.md", base / "CODEX.md"))
    if name == "claude":
        base = root / ".claude"
        return TargetSpec(name, base, base / "skills", (base / "CLAUDE.md",))
    raise SyncError(f"Unknown target {name!r}. Expected one of: {', '.join(TARGETS)}, all.")


def expand_targets(name: str) -> list[str]:
    if name == "all":
        return list(TARGETS)
    if name not in TARGETS:
        raise SyncError(f"Unknown target {name!r}. Expected one of: {', '.join(TARGETS)}, all.")
    return [name]


def skill_roots(env: str) -> list[Path]:
    roots = [agent_dir() / "skills-common", agent_dir() / f"skills-{env}"]
    return [root for root in roots if root.exists()]


def collect_skills(env: str) -> dict[str, SkillSource]:
    result: dict[str, SkillSource] = {}
    owners: dict[str, str] = {}
    for root in skill_roots(env):
        for child in sorted(root.iterdir(), key=lambda p: p.name):
            if not child.is_dir() or not (child / "SKILL.md").is_file():
                continue
            if child.name in result:
                raise SyncError(
                    f"Duplicate skill {child.name!r}: {owners[child.name]} and {rel(child)}."
                )
            result[child.name] = SkillSource(child.name, root, child)
            owners[child.name] = rel(child)
    return result


def matches_any(name: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(name, pattern) for pattern in patterns)


def compose_rules(env: str) -> str:
    common = (agent_dir() / "rules" / "common.md").read_text(encoding="utf-8")
    env_rules = (agent_dir() / "rules" / f"{env}.md").read_text(encoding="utf-8")
    return (
        "<!-- generated by docs/agent/sync.py; edit docs/agent/rules/*.md instead -->\n\n"
        f"# DicePP Agent Rules ({env})\n\n"
        "## Common\n\n"
        f"{common.rstrip()}\n\n"
        f"## Environment: {env}\n\n"
        f"{env_rules.rstrip()}\n"
    )


def state_path(spec: TargetSpec) -> Path:
    return spec.root / STATE_FILE


def load_state(spec: TargetSpec) -> dict[str, Any]:
    return load_json(state_path(spec))


def write_state(spec: TargetSpec, state: dict[str, Any], dry_run: bool) -> None:
    if dry_run:
        print(f"DRY-RUN write state: {rel(state_path(spec))}")
        return
    spec.root.mkdir(parents=True, exist_ok=True)
    state_path(spec).write_text(
        json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def ensure_inside(path: Path, parent: Path) -> None:
    path_abs = (path if path.is_absolute() else (Path.cwd() / path)).absolute()
    parent_abs = (parent if parent.is_absolute() else (Path.cwd() / parent)).absolute()
    try:
        path_abs.relative_to(parent_abs)
    except ValueError as exc:
        raise SyncError(f"Refusing to modify path outside {rel(parent)}: {path}") from exc


def remove_path(path: Path, dry_run: bool) -> None:
    if dry_run:
        print(f"DRY-RUN remove: {rel(path)}")
        return
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif is_windows_reparse_point(path):
        os.rmdir(path)
    elif path.is_dir():
        shutil.rmtree(path)


def is_windows_reparse_point(path: Path) -> bool:
    if os.name != "nt":
        return False
    try:
        attrs = path.stat(follow_symlinks=False).st_file_attributes
    except OSError:
        return False
    return bool(attrs & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def replace_dir_symlink(path: Path, dry_run: bool) -> None:
    if path.exists() or path.is_symlink():
        if path.is_symlink():
            remove_path(path, dry_run)
        elif path.is_file():
            raise SyncError(f"Expected a directory, found file: {rel(path)}")
    if dry_run:
        print(f"DRY-RUN ensure directory: {rel(path)}")
    else:
        path.mkdir(parents=True, exist_ok=True)


def create_junction(link: Path, target: Path) -> None:
    completed = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise OSError(completed.stderr.strip() or completed.stdout.strip() or "mklink failed")


def create_link_or_copy(source: Path, dest: Path, mode: str, dry_run: bool) -> str:
    if dry_run:
        effective = effective_link_mode(mode)
        print(f"DRY-RUN {effective}: {rel(dest)} -> {rel(source)}")
        return effective

    dest.parent.mkdir(parents=True, exist_ok=True)
    modes = [effective_link_mode(mode)]
    if mode == "auto" and modes[0] != "copy":
        modes.append("copy")

    errors: list[str] = []
    for candidate in modes:
        try:
            if candidate == "symlink":
                dest.symlink_to(source, target_is_directory=True)
            elif candidate == "junction":
                create_junction(dest, source)
            elif candidate == "copy":
                shutil.copytree(source, dest)
            else:
                raise SyncError(f"Unsupported link mode: {candidate}")
            return candidate
        except OSError as exc:
            errors.append(f"{candidate}: {exc}")
            if dest.exists() or dest.is_symlink():
                remove_path(dest, dry_run=False)

    raise SyncError(f"Failed to link {rel(dest)} -> {rel(source)} ({'; '.join(errors)})")


def effective_link_mode(mode: str) -> str:
    if mode != "auto":
        return mode
    if os.name == "nt":
        return "junction"
    return "symlink"


def same_target(entry: Path, source: Path) -> bool:
    if not (entry.exists() or entry.is_symlink()):
        return False
    try:
        return entry.resolve(strict=True) == source.resolve(strict=True)
    except OSError:
        return False


def is_legacy_skill_link(entry: Path, name: str) -> bool:
    if not entry.is_symlink():
        return False
    legacy = agent_dir() / "skills" / name
    try:
        return entry.resolve(strict=False) == legacy.absolute()
    except OSError:
        return False


def write_rules(spec: TargetSpec, env: str, dry_run: bool) -> list[str]:
    content = compose_rules(env)
    written: list[str] = []
    for rule_file in spec.rule_files:
        ensure_inside(rule_file, spec.root)
        if rule_file.parent.exists() and rule_file.parent.is_symlink():
            remove_path(rule_file.parent, dry_run)
        if rule_file.is_symlink():
            remove_path(rule_file, dry_run)
        elif rule_file.exists() and rule_file.is_dir():
            raise SyncError(f"Expected a rule file, found directory: {rel(rule_file)}")
        if dry_run:
            print(f"DRY-RUN write rules: {rel(rule_file)}")
        else:
            rule_file.parent.mkdir(parents=True, exist_ok=True)
            rule_file.write_text(content, encoding="utf-8")
        written.append(rel(rule_file))
    cleanup_legacy_rule_files(spec, dry_run)
    return written


def cleanup_legacy_rule_files(spec: TargetSpec, dry_run: bool) -> None:
    legacy_by_target: dict[str, list[Path]] = {}
    for legacy_file in legacy_by_target.get(spec.name, []):
        if legacy_file in spec.rule_files:
            continue
        ensure_inside(legacy_file, spec.root)
        if legacy_file.exists() or legacy_file.is_symlink():
            if legacy_file.is_dir() and not legacy_file.is_symlink():
                continue
            remove_path(legacy_file, dry_run)


def sync_skills(spec: TargetSpec, config: Config, dry_run: bool) -> dict[str, Any]:
    skills = collect_skills(config.env)
    ignored_conflicts = [name for name in skills if matches_any(name, config.ignore_skills)]
    if ignored_conflicts:
        raise SyncError(
            "ignore.skills conflicts with managed skills: " + ", ".join(sorted(ignored_conflicts))
        )

    state = load_state(spec)
    old_managed = state.get("managedSkills", {})
    if not isinstance(old_managed, dict):
        old_managed = {}

    spec.root.mkdir(parents=True, exist_ok=True)
    replace_dir_symlink(spec.skills_dir, dry_run)
    if not dry_run:
        spec.skills_dir.mkdir(parents=True, exist_ok=True)

    managed: dict[str, dict[str, str]] = {}

    for name, source in skills.items():
        dest = spec.skills_dir / name
        ensure_inside(dest, spec.skills_dir)
        previously_managed = name in old_managed

        if dest.exists() or dest.is_symlink():
            if same_target(dest, source.path):
                mode = old_managed.get(name, {}).get("mode") or "existing"
                managed[name] = {"source": rel(source.path), "mode": str(mode)}
                continue
            if previously_managed or is_legacy_skill_link(dest, name):
                remove_path(dest, dry_run)
            else:
                raise SyncError(
                    f"Refusing to replace untracked skill {rel(dest)}. "
                    f"Add it to {ENV_FILE} ignore.skills or move it aside."
                )

        mode = create_link_or_copy(source.path, dest, config.link_mode, dry_run)
        managed[name] = {"source": rel(source.path), "mode": mode}

    for name in sorted(old_managed):
        if name in skills or matches_any(name, config.ignore_skills):
            continue
        dest = spec.skills_dir / name
        ensure_inside(dest, spec.skills_dir)
        if dest.exists() or dest.is_symlink():
            remove_path(dest, dry_run)

    return managed


def merge_claude_linux_settings(spec: TargetSpec, state: dict[str, Any], dry_run: bool) -> dict[str, Any]:
    if spec.name != "claude" or platform.system() != "Linux":
        return {}

    source_path = agent_dir() / "platforms" / "claude-linux" / "settings.json"
    if not source_path.is_file():
        return {}

    source = load_json(source_path)
    target_path = spec.root / "settings.json"
    current = load_json(target_path)
    previous = state.get("claudeLinuxSettings", {})
    if not isinstance(previous, dict):
        previous = {}

    merged = copy.deepcopy(current)
    warnings: list[str] = []

    for key, value in source.items():
        if key == "hooks":
            continue
        if key not in merged:
            merged[key] = value
        elif merged[key] == value:
            pass
        elif previous.get(key) == merged[key]:
            merged[key] = value
        else:
            warnings.append(f"preserved existing .claude/settings.json key {key!r}")

    source_hooks = source.get("hooks")
    if isinstance(source_hooks, dict):
        merged_hooks = merged.setdefault("hooks", {})
        if not isinstance(merged_hooks, dict):
            warnings.append("preserved existing non-object hooks value")
        else:
            previous_hooks = previous.get("hooks", {})
            for hook_name, source_entries in source_hooks.items():
                if not isinstance(source_entries, list):
                    continue
                current_entries = merged_hooks.get(hook_name)
                previous_entries = {}
                if isinstance(previous_hooks, dict):
                    previous_entries = previous_hooks.get(hook_name, [])
                if current_entries is None:
                    merged_hooks[hook_name] = source_entries
                    continue
                if not isinstance(current_entries, list):
                    warnings.append(f"preserved existing hooks.{hook_name} because it is not a list")
                    continue
                merged_hooks[hook_name] = merge_hook_entries(
                    hook_name, current_entries, source_entries, previous_entries, warnings
                )

    if merged != current:
        if dry_run:
            print(f"DRY-RUN merge Claude Linux settings: {rel(target_path)}")
        else:
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_text(
                json.dumps(merged, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

    for warning in warnings:
        print(f"warning: {warning}")

    return source


def merge_hook_entries(
    hook_name: str,
    current_entries: list[Any],
    source_entries: list[Any],
    previous_entries: Any,
    warnings: list[str],
) -> list[Any]:
    result = copy.deepcopy(current_entries)
    previous_by_matcher: dict[str, Any] = {}
    if isinstance(previous_entries, list):
        for entry in previous_entries:
            if isinstance(entry, dict) and isinstance(entry.get("matcher"), str):
                previous_by_matcher[entry["matcher"]] = entry

    current_by_matcher: dict[str, int] = {}
    for index, entry in enumerate(result):
        if isinstance(entry, dict) and isinstance(entry.get("matcher"), str):
            current_by_matcher[entry["matcher"]] = index

    for source_entry in source_entries:
        if not isinstance(source_entry, dict) or not isinstance(source_entry.get("matcher"), str):
            if source_entry not in result:
                result.append(source_entry)
            continue
        matcher = source_entry["matcher"]
        if matcher not in current_by_matcher:
            result.append(source_entry)
            continue
        index = current_by_matcher[matcher]
        current_entry = result[index]
        previous_entry = previous_by_matcher.get(matcher)
        if current_entry == source_entry:
            continue
        if previous_entry == current_entry:
            result[index] = source_entry
        else:
            warnings.append(
                f"preserved existing hooks.{hook_name} matcher {matcher!r}; "
                "it differs from the managed Claude Linux hook"
            )
    return result


def apply_target(target: str, env_arg: str | None, dry_run: bool, link_mode: str | None) -> None:
    config = resolve_config(env_arg, target)
    if link_mode:
        config.link_mode = link_mode
    validate_env(config.env)
    spec = target_spec(target)

    print(f"== {target} ==")
    print(f"repo: {repo_root()}")
    print(f"env: {config.env}")
    print(f"linkMode: {config.link_mode} ({effective_link_mode(config.link_mode)})")
    print("rules:")
    for rule in spec.rule_files:
        print(f"  - {rel(rule)}")
    print("skills:")
    for skill in sorted(collect_skills(config.env)):
        print(f"  - {skill}")
    if config.ignore_skills:
        print("ignored skill patterns:")
        for pattern in config.ignore_skills:
            print(f"  - {pattern}")

    state = load_state(spec)
    rule_files = write_rules(spec, config.env, dry_run)
    managed_skills = sync_skills(spec, config, dry_run)
    claude_linux_settings = merge_claude_linux_settings(spec, state, dry_run)

    new_state = {
        "version": 1,
        "source": rel(agent_dir()),
        "target": target,
        "env": config.env,
        "platform": platform.system(),
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "ruleFiles": rule_files,
        "skillRoots": [rel(root) for root in skill_roots(config.env)],
        "managedSkills": managed_skills,
        "ignoreSkills": config.ignore_skills,
    }
    if claude_linux_settings:
        new_state["claudeLinuxSettings"] = claude_linux_settings
    write_state(spec, new_state, dry_run)


def classify_target(target: str, env_arg: str | None) -> tuple[list[str], list[str]]:
    warnings: list[str] = []
    errors: list[str] = []
    try:
        config = resolve_config(env_arg, target)
        validate_env(config.env)
        spec = target_spec(target)
        skills = collect_skills(config.env)
    except SyncError as exc:
        return [], [str(exc)]

    state = load_state(spec)
    old_managed = state.get("managedSkills", {})
    if not isinstance(old_managed, dict):
        old_managed = {}

    if state and state.get("env") != config.env:
        warnings.append(
            f"{target}: state env is {state.get('env')!r}, current env is {config.env!r}"
        )

    for rule in spec.rule_files:
        if not rule.exists():
            warnings.append(f"{target}: missing rule file {rel(rule)}")

    if spec.skills_dir.is_symlink() and not spec.skills_dir.exists():
        errors.append(f"{target}: skills dir is a broken link {rel(spec.skills_dir)}")
    elif spec.skills_dir.exists() or spec.skills_dir.is_symlink():
        if spec.skills_dir.is_symlink():
            warnings.append(f"{target}: skills dir is a symlink; apply will replace it")
        for entry in sorted(spec.skills_dir.iterdir(), key=lambda p: p.name):
            name = entry.name
            if name in skills:
                source = skills[name].path
                if not same_target(entry, source):
                    if name in old_managed:
                        warnings.append(f"{target}: managed skill {name} is out of sync")
                    elif is_legacy_skill_link(entry, name):
                        warnings.append(f"{target}: legacy skill link {name} will be replaced")
                    else:
                        errors.append(f"{target}: skill {name} conflicts with managed source")
                if entry.is_symlink() and not entry.exists() and not is_legacy_skill_link(entry, name):
                    errors.append(f"{target}: skill {name} is a broken link")
            elif matches_any(name, config.ignore_skills):
                continue
            elif name in old_managed:
                warnings.append(f"{target}: stale managed skill {name}")
            else:
                warnings.append(f"{target}: unknown skill {name}")
    else:
        warnings.append(f"{target}: missing skills dir {rel(spec.skills_dir)}")

    return warnings, errors


def report_target(target: str, env_arg: str | None) -> None:
    config = resolve_config(env_arg, target)
    validate_env(config.env)
    spec = target_spec(target)
    skills = collect_skills(config.env)
    state = load_state(spec)
    old_managed = state.get("managedSkills", {})
    if not isinstance(old_managed, dict):
        old_managed = {}

    print(f"== {target} ==")
    print(f"root: {rel(spec.root)}")
    print(f"env: {config.env}")
    print(f"state env: {state.get('env', '<none>') if state else '<none>'}")
    print(f"linkMode: {config.link_mode} ({effective_link_mode(config.link_mode)})")
    peer = config.raw.get("peer", {})
    if isinstance(peer, dict) and peer:
        print("peer paths:")
        for key in sorted(peer):
            value = peer[key]
            if isinstance(value, str) and value:
                print(f"  - {key}: {value}")
    else:
        print("peer paths: <none>")
    print("rule files:")
    for rule in spec.rule_files:
        status = "exists" if rule.exists() else "missing"
        print(f"  - {rel(rule)} [{status}]")
    print("skill roots:")
    for root in skill_roots(config.env):
        print(f"  - {rel(root)}")
    print("managed source skills:")
    for name, source in sorted(skills.items()):
        print(f"  - {name}: {rel(source.path)}")
    if config.ignore_skills:
        print("ignored skill patterns:")
        for pattern in config.ignore_skills:
            print(f"  - {pattern}")
    print("target skills:")
    if spec.skills_dir.is_symlink() and not spec.skills_dir.exists():
        print(f"  <broken link: {rel(spec.skills_dir)}>")
    elif spec.skills_dir.exists() or spec.skills_dir.is_symlink():
        for entry in sorted(spec.skills_dir.iterdir(), key=lambda p: p.name):
            name = entry.name
            if name in skills:
                status = "ok" if same_target(entry, skills[name].path) else "mismatch"
            elif matches_any(name, config.ignore_skills):
                status = "ignored"
            elif name in old_managed:
                status = "stale-managed"
            else:
                status = "unknown"
            suffix = " broken-link" if entry.is_symlink() and not entry.exists() else ""
            print(f"  - {name}: {status}{suffix}")
    else:
        print(f"  <missing: {rel(spec.skills_dir)}>")


def print_help_text() -> None:
    print(
        f"""DicePP agent sync

Purpose:
  Synchronize docs/agent rules and skills into local agent tool directories.
  The source of truth is docs/agent; .codex/.claude are working
  directories managed by this script.

Environment:
  The local environment is read from {rel(agent_dir() / ENV_FILE)} unless --env is passed.
  The file is local-only. A development checkout may look like:

    {{
      "env": "dev",
      "linkMode": "auto",
      "peer": {{
        "prodRoot": "/path/to/production/checkout"
      }},
      "ignore": {{
        "skills": ["local-*"]
      }}
    }}

  A production checkout may use peer.devRoot instead:

    {{
      "env": "prod",
      "linkMode": "auto",
      "peer": {{
        "devRoot": "/path/to/development/checkout"
      }}
    }}

Directory convention:
  rules/common.md      shared rules
  rules/<env>.md       environment-specific rules
  skills-common/       skills exposed in every environment
  skills-<env>/        skills exposed only in that environment
  platforms/           platform-specific extras; currently claude-linux settings/hooks

Optional peer paths:
  The peer block is local-only and optional. Handoff skills may use peer.devRoot
  or peer.prodRoot for cross-environment handoff and read-only evidence checks.
  sync.py records no peer state and does not write peer directories by itself.

Targets:
  codex   -> .codex/AGENTS.md, .codex/CODEX.md, .codex/skills/
  claude  -> .claude/CLAUDE.md, .claude/skills/
  all     -> all targets above

Commands:
  help      Show this self-description.
  report    Print the current effective environment, source skills, target files,
            target skill status, ignored local skills, and previous sync state.
  doctor    Check for missing rules, stale managed skills, broken links, unknown
            target skills, ignored local skills, and environment mismatches.
  apply     Generate rule files and synchronize managed skills for a target.

Notes:
  - apply preserves target skills matching ignore.skills.
  - apply only removes stale skills previously recorded in {STATE_FILE}.
  - On Windows, linkMode auto prefers junctions. On Linux/macOS it prefers symlinks.
  - If linking fails, auto falls back to copying and records that in sync state.
  - Claude Linux settings are merged conservatively; existing unrelated settings
    are preserved, and conflicting existing settings are reported instead of
    overwritten.
"""
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Synchronize DicePP agent rules and skills.",
        add_help=True,
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("help", help="show the sync.py self-description")

    for command in ("report", "doctor", "apply"):
        p = sub.add_parser(command)
        p.add_argument("target", choices=(*TARGETS, "all"))
        p.add_argument("--env", choices=None)
        if command == "apply":
            p.add_argument("--dry-run", action="store_true")
            p.add_argument("--link-mode", choices=("auto", "symlink", "junction", "copy"))

    return parser


def main(argv: list[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command in (None, "help"):
        print_help_text()
        return 0

    try:
        if args.command == "apply":
            for target in expand_targets(args.target):
                apply_target(target, args.env, args.dry_run, args.link_mode)
            return 0

        if args.command == "report":
            for target in expand_targets(args.target):
                report_target(target, args.env)
            return 0

        if args.command == "doctor":
            all_warnings: list[str] = []
            all_errors: list[str] = []
            for target in expand_targets(args.target):
                warnings, errors = classify_target(target, args.env)
                all_warnings.extend(warnings)
                all_errors.extend(errors)
            if not all_warnings and not all_errors:
                print("doctor: ok")
                return 0
            for warning in all_warnings:
                print(f"warning: {warning}")
            for error in all_errors:
                print(f"error: {error}")
            return 1 if all_errors else 0

        parser.error(f"Unknown command: {args.command}")
        return 2
    except SyncError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
