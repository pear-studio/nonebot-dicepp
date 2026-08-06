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
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


TARGETS = ("codex", "claude", "kimi")
STATE_FILE = ".agent-sync.json"
ENV_FILE = ".agent-env.json"
MANIFEST_FILE = "manifest.json"


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


@dataclass(frozen=True)
class GlobalTargetSpec:
    name: str
    skills_dir: Path
    agents: tuple[str, ...]


@dataclass
class GlobalPlan:
    targets: list[GlobalTargetSpec]
    provider_roots: list[Path]
    linked: list[tuple[GlobalTargetSpec, SkillSource, Path]]
    provided: list[tuple[GlobalTargetSpec, SkillSource, Path, Path]]
    missing: list[tuple[GlobalTargetSpec, SkillSource, Path]]
    blocked: list[tuple[GlobalTargetSpec, SkillSource, Path, str]]
    stale: list[tuple[GlobalTargetSpec, Path]]
    conflicts: list[tuple[GlobalTargetSpec, SkillSource, Path]]


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


def user_home() -> Path:
    return Path.home()


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
    if name == "kimi":
        base = root / ".kimi-code"
        return TargetSpec(name, base, base / "skills", (base / "AGENTS.md",))
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


def all_skill_source_roots(root: Path | None = None) -> list[Path]:
    base = root or agent_dir()
    return sorted(
        (path for path in base.glob("skills-*") if path.is_dir()),
        key=lambda path: path.name,
    )


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


def load_agent_manifest(root: Path | None = None) -> dict[str, Any]:
    manifest_path = (root or agent_dir()) / MANIFEST_FILE
    manifest = load_json(manifest_path)
    repository = manifest.get("repository")
    if not isinstance(repository, str) or not repository.strip():
        raise SyncError(f"{rel(manifest_path)} must contain a non-empty repository string.")
    return manifest


def repository_id(root: Path | None = None) -> str:
    return str(load_agent_manifest(root)["repository"]).strip()


def load_global_skill_names(root: Path | None = None) -> list[str]:
    manifest_path = (root or agent_dir()) / MANIFEST_FILE
    manifest = load_agent_manifest(root)
    global_block = manifest.get("global")
    if not isinstance(global_block, dict):
        raise SyncError(f"{rel(manifest_path)} must contain a global object.")
    names = global_block.get("skills")
    if not isinstance(names, list):
        raise SyncError(f"{rel(manifest_path)} global.skills must be a list.")

    result: list[str] = []
    for name in names:
        if (
            not isinstance(name, str)
            or not name
            or name.strip() != name
            or name in {".", ".."}
            or "/" in name
            or "\\" in name
            or Path(name).name != name
        ):
            raise SyncError(f"Invalid global skill name in {rel(manifest_path)}: {name!r}.")
        if name in result:
            raise SyncError(f"Duplicate global skill {name!r} in {rel(manifest_path)}.")
        result.append(name)
    return result


def collect_global_skills(root: Path | None = None) -> dict[str, SkillSource]:
    base = root or agent_dir()
    roots = all_skill_source_roots(base)
    result: dict[str, SkillSource] = {}
    for name in load_global_skill_names(base):
        matches = [
            SkillSource(name, skill_root, skill_root / name)
            for skill_root in roots
            if (skill_root / name).is_dir()
            and (skill_root / name / "SKILL.md").is_file()
        ]
        if not matches:
            raise SyncError(
                f"Global skill {name!r} must exist under one {rel(base)}/skills-* "
                "directory with a SKILL.md file."
            )
        if len(matches) > 1:
            locations = ", ".join(rel(source.path) for source in matches)
            raise SyncError(f"Global skill {name!r} has multiple sources: {locations}.")
        result[name] = matches[0]
    return result


def agent_is_detected(name: str) -> bool:
    home = user_home()
    config_dirs = {
        "codex": home / ".codex",
        "claude": home / ".claude",
        "kimi": home / ".kimi-code",
    }
    commands = {"codex": "codex", "claude": "claude", "kimi": "kimi"}
    return config_dirs[name].exists() or shutil.which(commands[name]) is not None


def global_target_specs() -> list[GlobalTargetSpec]:
    home = user_home()
    specs: list[GlobalTargetSpec] = []
    shared_agents = tuple(name for name in ("codex", "kimi") if agent_is_detected(name))
    if shared_agents or (home / ".agents").exists():
        specs.append(GlobalTargetSpec("agents", home / ".agents" / "skills", shared_agents))
    if agent_is_detected("claude"):
        specs.append(GlobalTargetSpec("claude", home / ".claude" / "skills", ("claude",)))
    return specs


def global_skills_dir_for_target(target: str) -> Path:
    home = user_home()
    if target in {"codex", "kimi"}:
        return home / ".agents" / "skills"
    if target == "claude":
        return home / ".claude" / "skills"
    raise SyncError(f"Unknown target {target!r}.")


def collect_project_skills(env: str, target: str) -> dict[str, SkillSource]:
    skills = collect_skills(env)
    global_skills = collect_global_skills()
    global_dir = global_skills_dir_for_target(target)
    return {
        name: source
        for name, source in skills.items()
        if name not in global_skills
        or not global_skill_available(global_dir / name, source)
    }


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


def is_windows_reparse_point(path: Path) -> bool:
    if os.name != "nt":
        return False
    try:
        attrs = path.stat(follow_symlinks=False).st_file_attributes
    except OSError:
        return False
    return bool(attrs & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def path_entry_exists(path: Path) -> bool:
    return path.exists() or path.is_symlink() or is_windows_reparse_point(path)


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


def replace_dir_symlink(path: Path, dry_run: bool) -> None:
    if path_entry_exists(path):
        if path.is_symlink() or is_windows_reparse_point(path):
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
            if path_entry_exists(dest):
                remove_path(dest, dry_run=False)

    raise SyncError(f"Failed to link {rel(dest)} -> {rel(source)} ({'; '.join(errors)})")


def effective_link_mode(mode: str) -> str:
    if mode != "auto":
        return mode
    if os.name == "nt":
        return "junction"
    return "symlink"


def same_target(entry: Path, source: Path) -> bool:
    if not path_entry_exists(entry):
        return False
    try:
        return entry.resolve(strict=True) == source.resolve(strict=True)
    except OSError:
        return False


def linked_skill_provider(entry: Path, name: str) -> Path | None:
    if not (entry.is_symlink() or is_windows_reparse_point(entry)):
        return None
    try:
        resolved = entry.resolve(strict=True)
    except OSError:
        return None
    if (
        resolved.name != name
        or not resolved.parent.name.startswith("skills-")
        or not (resolved / "SKILL.md").is_file()
    ):
        return None

    provider_agent_dir = resolved.parent.parent
    try:
        if repository_id(provider_agent_dir) != repository_id():
            return None
        provider_source = collect_global_skills(provider_agent_dir).get(name)
        if provider_source is None or provider_source.path.resolve(strict=True) != resolved:
            return None
    except SyncError:
        return None
    return resolved


def global_skill_available(entry: Path, source: SkillSource) -> bool:
    return same_target(entry, source.path) or linked_skill_provider(entry, source.name) is not None


def git_checkout_label(root: Path) -> str:
    branch = subprocess.run(
        ["git", "-C", str(root), "branch", "--show-current"],
        check=False,
        capture_output=True,
        text=True,
    )
    commit = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    if commit.returncode != 0:
        return "not a Git checkout"
    branch_name = branch.stdout.strip() if branch.returncode == 0 else ""
    return f"{branch_name or 'detached'}@{commit.stdout.strip()}"


def global_link_mode(os_name: str | None = None) -> str:
    return "junction" if (os_name or os.name) == "nt" else "symlink"


def create_global_link(source: Path, dest: Path, dry_run: bool) -> None:
    mode = global_link_mode()
    if dry_run:
        print(f"DRY-RUN {mode}: {dest} -> {source}")
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        if mode == "junction":
            create_junction(dest, source.resolve(strict=True))
        else:
            dest.symlink_to(source.resolve(strict=True), target_is_directory=True)
    except OSError as exc:
        raise SyncError(
            f"Failed to create global {mode} {dest} -> {source}: {exc}. "
            "Global skills require a link and will not fall back to copying."
        ) from exc


def relink_global_skill(source: Path, dest: Path, provider: Path, dry_run: bool) -> None:
    if dry_run:
        print(f"DRY-RUN relink: {dest}: {provider} -> {source}")
        return
    if linked_skill_provider(dest, source.name) != provider:
        raise SyncError(f"Global provider changed while preparing to relink {dest}.")

    token = uuid.uuid4().hex
    pending = dest.with_name(f".{dest.name}.agent-sync-new-{token}")
    backup = dest.with_name(f".{dest.name}.agent-sync-old-{token}")
    ensure_inside(pending, dest.parent)
    ensure_inside(backup, dest.parent)
    create_global_link(source, pending, dry_run=False)
    try:
        dest.rename(backup)
        pending.rename(dest)
        remove_path(backup, dry_run=False)
    except OSError as exc:
        if not path_entry_exists(dest) and path_entry_exists(backup):
            backup.rename(dest)
        if path_entry_exists(pending):
            remove_path(pending, dry_run=False)
        raise SyncError(f"Failed to relink global skill {dest}: {exc}") from exc


def is_repo_skill_link(entry: Path) -> bool:
    if not (entry.is_symlink() or is_windows_reparse_point(entry)):
        return False
    try:
        resolved = entry.resolve(strict=True)
    except OSError:
        return False
    for root in all_skill_source_roots():
        try:
            relative = resolved.relative_to(root.resolve(strict=True))
        except (OSError, ValueError):
            continue
        return (
            len(relative.parts) == 1
            and relative.name == entry.name
            and (resolved / "SKILL.md").is_file()
        )
    return False


def build_global_plan(specs: list[GlobalTargetSpec] | None = None) -> GlobalPlan:
    linked: list[tuple[GlobalTargetSpec, SkillSource, Path]] = []
    provided: list[tuple[GlobalTargetSpec, SkillSource, Path, Path]] = []
    missing: list[tuple[GlobalTargetSpec, SkillSource, Path]] = []
    blocked: list[tuple[GlobalTargetSpec, SkillSource, Path, str]] = []
    stale: list[tuple[GlobalTargetSpec, Path]] = []
    conflicts: list[tuple[GlobalTargetSpec, SkillSource, Path]] = []
    sources = collect_global_skills()
    targets = specs if specs is not None else global_target_specs()
    provider_roots: set[Path] = set()

    for spec in targets:
        if not spec.skills_dir.exists() or not spec.skills_dir.is_dir():
            continue
        for entry in spec.skills_dir.iterdir():
            provider = linked_skill_provider(entry, entry.name)
            if provider is not None:
                provider_roots.add(provider.parent.parent.resolve(strict=True))

    selected_provider = next(iter(provider_roots)) if len(provider_roots) == 1 else None
    provider_sources: dict[str, SkillSource] = {}
    if selected_provider is not None:
        try:
            provider_sources = collect_global_skills(selected_provider)
        except SyncError:
            provider_sources = {}

    for spec in targets:
        for source in sources.values():
            dest = spec.skills_dir / source.name
            if not path_entry_exists(dest):
                if selected_provider is None and provider_roots:
                    blocked.append((spec, source, dest, "multiple provider checkouts are active"))
                    continue
                provider_source = source
                if selected_provider is not None and selected_provider != agent_dir().resolve():
                    provider_source = provider_sources.get(source.name)
                    if provider_source is None:
                        blocked.append(
                            (spec, source, dest, "the provider checkout does not expose this skill")
                        )
                        continue
                missing.append((spec, provider_source, dest))
            elif same_target(dest, source.path):
                linked.append((spec, source, dest))
            else:
                provider = linked_skill_provider(dest, source.name)
                if provider is not None:
                    provided.append((spec, source, dest, provider))
                else:
                    conflicts.append((spec, source, dest))

        if spec.skills_dir.exists() and spec.skills_dir.is_dir():
            for entry in sorted(spec.skills_dir.iterdir(), key=lambda path: path.name):
                if entry.name not in sources and is_repo_skill_link(entry):
                    stale.append((spec, entry))

    return GlobalPlan(
        targets=targets,
        provider_roots=sorted(provider_roots, key=str),
        linked=linked,
        provided=provided,
        missing=missing,
        blocked=blocked,
        stale=stale,
        conflicts=conflicts,
    )


def print_global_plan(plan: GlobalPlan) -> None:
    print("== global ==")
    if not plan.targets:
        print("detected targets: <none>")
        return
    print("detected targets:")
    for spec in plan.targets:
        agents = ", ".join(spec.agents) if spec.agents else "generic"
        print(f"  - {spec.name}: {spec.skills_dir} ({agents})")
    print("provider checkout:")
    if not plan.provider_roots:
        print("  - <not selected>")
    for provider_agent_dir in plan.provider_roots:
        checkout_root = provider_agent_dir.parent.parent
        print(f"  - {checkout_root} [{git_checkout_label(checkout_root)}]")
    for label, group in (
        ("linked", plan.linked),
        ("missing", plan.missing),
        ("conflicts", plan.conflicts),
    ):
        print(f"{label}:")
        if not group:
            print("  - <none>")
        for spec, source, dest in group:
            print(f"  - {spec.name}/{source.name}: {dest} -> {source.path}")
    print("provided by other checkout:")
    if not plan.provided:
        print("  - <none>")
    for spec, source, dest, provider in plan.provided:
        provider_root = provider.parent.parent.parent.parent
        print(
            f"  - {spec.name}/{source.name}: {dest} -> {provider} "
            f"[{git_checkout_label(provider_root)}]"
        )
    print("blocked:")
    if not plan.blocked:
        print("  - <none>")
    for spec, source, dest, reason in plan.blocked:
        print(f"  - {spec.name}/{source.name}: {dest} ({reason})")
    print("stale:")
    if not plan.stale:
        print("  - <none>")
    for spec, entry in plan.stale:
        print(f"  - {spec.name}/{entry.name}: {entry}")


def apply_global(dry_run: bool, relink: bool = False) -> None:
    plan = build_global_plan()
    print_global_plan(plan)
    if plan.conflicts:
        names = ", ".join(f"{spec.name}/{source.name}" for spec, source, _ in plan.conflicts)
        raise SyncError(f"Refusing to overwrite conflicting global skills: {names}.")
    if not relink and (len(plan.provider_roots) > 1 or plan.blocked):
        raise SyncError(
            "Global provider checkout is inconsistent or missing required skills. "
            "Update the current provider, or explicitly switch with --relink."
        )

    current_sources = collect_global_skills()
    for spec, source, dest in plan.missing:
        ensure_inside(dest, spec.skills_dir)
        link_source = current_sources[source.name] if relink else source
        create_global_link(link_source.path, dest, dry_run)
    if relink:
        for spec, source, dest, _reason in plan.blocked:
            ensure_inside(dest, spec.skills_dir)
            create_global_link(current_sources[source.name].path, dest, dry_run)
    if relink:
        for spec, source, dest, provider in plan.provided:
            ensure_inside(dest, spec.skills_dir)
            relink_global_skill(source.path, dest, provider, dry_run)
    for spec, entry in plan.stale:
        ensure_inside(entry, spec.skills_dir)
        if not is_repo_skill_link(entry):
            raise SyncError(f"Refusing to remove unrecognized global skill link: {entry}")
        if dry_run:
            print(f"DRY-RUN remove stale global skill: {entry}")
        else:
            print(f"remove stale global skill: {entry}")
            remove_path(entry, dry_run=False)


def report_global() -> None:
    print_global_plan(build_global_plan())


def classify_global() -> tuple[list[str], list[str]]:
    plan = build_global_plan()
    warnings = [
        f"global: missing {spec.name}/{source.name}"
        for spec, source, _dest in plan.missing
    ]
    warnings.extend(f"global: stale {spec.name}/{entry.name}" for spec, entry in plan.stale)
    errors = [
        f"global: conflict {spec.name}/{source.name} at {dest}"
        for spec, source, dest in plan.conflicts
    ]
    if len(plan.provider_roots) > 1:
        errors.append("global: skills are split across multiple provider checkouts")
    errors.extend(
        f"global: blocked {spec.name}/{source.name}: {reason}"
        for spec, source, _dest, reason in plan.blocked
    )
    return warnings, errors


def print_global_advisory() -> None:
    plan = build_global_plan()
    if (
        not plan.missing
        and not plan.stale
        and not plan.conflicts
        and not plan.blocked
        and len(plan.provider_roots) <= 1
    ):
        print("global: ok")
        return
    parts: list[str] = []
    if plan.missing:
        parts.append(f"{len(plan.missing)} missing")
    if plan.stale:
        parts.append(f"{len(plan.stale)} stale")
    if plan.conflicts:
        parts.append(f"{len(plan.conflicts)} conflicts")
    provider_issues = len(plan.blocked) + (1 if len(plan.provider_roots) > 1 else 0)
    if provider_issues:
        parts.append(f"{provider_issues} provider issues")
    print(f"global: {', '.join(parts)}; inspect with `sync.py report global`")


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
        if path_entry_exists(legacy_file):
            if (
                legacy_file.is_dir()
                and not legacy_file.is_symlink()
                and not is_windows_reparse_point(legacy_file)
            ):
                continue
            remove_path(legacy_file, dry_run)


def sync_skills(spec: TargetSpec, config: Config, dry_run: bool) -> dict[str, Any]:
    all_skills = collect_skills(config.env)
    skills = collect_project_skills(config.env, spec.name)
    ignored_conflicts = [name for name in all_skills if matches_any(name, config.ignore_skills)]
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

        if path_entry_exists(dest):
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
        if path_entry_exists(dest):
            if dry_run:
                print(f"DRY-RUN remove stale skill: {rel(dest)}")
            else:
                print(f"remove stale skill: {rel(dest)}")
                remove_path(dest, dry_run=False)

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
    project_skills = collect_project_skills(config.env, target)
    for skill in sorted(project_skills):
        print(f"  - {skill}")
    global_skills = sorted(set(collect_skills(config.env)) - set(project_skills))
    if global_skills:
        print("skills provided globally:")
        for skill in global_skills:
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
        skills = collect_project_skills(config.env, target)
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
        for name in sorted(skills):
            if matches_any(name, config.ignore_skills):
                continue
            if not path_entry_exists(spec.skills_dir / name):
                warnings.append(f"{target}: missing managed skill {name}")
    else:
        warnings.append(f"{target}: missing skills dir {rel(spec.skills_dir)}")

    return warnings, errors


def report_target(target: str, env_arg: str | None) -> None:
    config = resolve_config(env_arg, target)
    validate_env(config.env)
    spec = target_spec(target)
    all_skills = collect_skills(config.env)
    skills = collect_project_skills(config.env, target)
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
    globally_provided = sorted(set(all_skills) - set(skills))
    if globally_provided:
        print("globally provided skills:")
        for name in globally_provided:
            print(f"  - {name}: {global_skills_dir_for_target(target) / name}")
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
    if spec.skills_dir.exists():
        missing = [
            name
            for name in sorted(skills)
            if not matches_any(name, config.ignore_skills)
            and not path_entry_exists(spec.skills_dir / name)
        ]
        if missing:
            print("missing managed skills:")
            for name in missing:
                print(f"  - {name}: {rel(skills[name].path)}")


def print_help_text() -> None:
    print(
        f"""DicePP agent sync

Purpose:
  Synchronize docs/agent rules and skills into local agent tool directories.
  The source of truth is docs/agent; .codex/.claude/.kimi-code are working
  directories managed by this script.
  Skills listed in {MANIFEST_FILE} global.skills can also be linked from the
  repository into user-level agent skill directories.

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
  manifest.json        stable repository ID and repository-tracked global skill list
  rules/common.md      shared rules
  rules/<env>.md       environment-specific rules
  skills-common/       skills exposed in every environment
  skills-<env>/        skills exposed only in that environment
  Any unique skill under skills-* may also be selected for user-level global sync.
  platforms/           platform-specific extras; currently claude-linux settings/hooks

Optional peer paths:
  The peer block is local-only and optional. Handoff skills may use peer.devRoot
  or peer.prodRoot for cross-environment handoff and read-only evidence checks.
  sync.py records no peer state and does not write peer directories by itself.

Targets:
  codex   -> .codex/AGENTS.md, .codex/CODEX.md, .codex/skills/
  claude  -> .claude/CLAUDE.md, .claude/skills/
  kimi    -> .kimi-code/AGENTS.md, .kimi-code/skills/
  all     -> all targets above
  global  -> user-level shared skills for detected agents

Commands:
  help      Show this self-description.
  report    Print the current effective environment, source skills, target files,
            target skill status, missing managed skills, ignored local skills,
            and previous sync state.
  doctor    Check for missing rules, missing or stale managed skills, broken
            links, unknown target skills, ignored local skills, and environment
            mismatches.
  apply     Generate rule files and synchronize managed skills for a target.

Global workflow:
  report/doctor/apply on project targets also reports global status as an
  advisory. Project sync keeps a local skill projection until the matching
  global link exists, then removes the duplicate local projection.

  sync.py report global
  sync.py apply global --dry-run
  sync.py apply global
  sync.py doctor global

  If another checkout of the same repository already provides a skill, its link
  remains authoritative and is reported as provided-by-other-checkout. To move
  all such links to the current checkout after user confirmation:

  sync.py apply global --relink --dry-run
  sync.py apply global --relink

Notes:
  - apply preserves target skills matching ignore.skills.
  - apply only removes stale skills previously recorded in {STATE_FILE}.
  - On Windows, linkMode auto prefers junctions. On Linux/macOS it prefers symlinks.
  - If linking fails, auto falls back to copying and records that in sync state.
  - Global skills never fall back to copying. Windows uses junctions;
    Linux/macOS uses directory symlinks.
  - Global apply never overwrites conflicts. Running it is explicit approval to
    add missing links and remove stale links that point into this repository's
    skills-* source directories.
  - manifest.json repository identifies dev, prod, and worktree checkouts of the
    same repository. The first linked checkout remains the provider until an
    explicit --relink operation.
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
        p.add_argument("target", choices=(*TARGETS, "all", "global"))
        p.add_argument("--env", choices=None)
        if command == "apply":
            p.add_argument("--dry-run", action="store_true")
            p.add_argument("--link-mode", choices=("auto", "symlink", "junction", "copy"))
            p.add_argument(
                "--relink",
                action="store_true",
                help="move same-repository global links to the current checkout",
            )

    return parser


def main(argv: list[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command in (None, "help"):
        print_help_text()
        return 0

    try:
        if args.command == "apply":
            if args.target == "global":
                if args.link_mode:
                    raise SyncError("Global skills use a fixed link-only mode; omit --link-mode.")
                apply_global(args.dry_run, relink=args.relink)
                return 0
            if args.relink:
                raise SyncError("--relink is only valid with the global target.")
            for target in expand_targets(args.target):
                apply_target(target, args.env, args.dry_run, args.link_mode)
            print_global_advisory()
            return 0

        if args.command == "report":
            if args.target == "global":
                report_global()
                return 0
            for target in expand_targets(args.target):
                report_target(target, args.env)
            print_global_advisory()
            return 0

        if args.command == "doctor":
            if args.target == "global":
                all_warnings, all_errors = classify_global()
            else:
                all_warnings = []
                all_errors = []
                for target in expand_targets(args.target):
                    warnings, errors = classify_target(target, args.env)
                    all_warnings.extend(warnings)
                    all_errors.extend(errors)
                print_global_advisory()
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
