from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

from tests.support.paths import find_repository_root


REPO_ROOT = find_repository_root(Path(__file__))
SYNC_PATH = REPO_ROOT / "docs" / "agent" / "sync.py"


@pytest.fixture
def agent_sync():
    module_name = "dicepp_agent_sync_for_tests"
    spec = importlib.util.spec_from_file_location(module_name, SYNC_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def write_skill(root: Path, name: str) -> Path:
    path = root / name
    path.mkdir(parents=True)
    (path / "SKILL.md").write_text(f"---\nname: {name}\n---\n", encoding="utf-8")
    return path


def write_manifest(
    agent_root: Path,
    names: list[str],
    repository: str = "example/project",
) -> None:
    agent_root.mkdir(parents=True, exist_ok=True)
    (agent_root / "manifest.json").write_text(
        json.dumps({"repository": repository, "global": {"skills": names}}),
        encoding="utf-8",
    )


def create_directory_link(agent_sync, link: Path, target: Path) -> None:
    link.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        agent_sync.create_junction(link, target)
    else:
        link.symlink_to(target, target_is_directory=True)


def test_repository_global_manifest_resolves_common_skills(agent_sync) -> None:
    assert agent_sync.repository_id() == "pear-studio/nonebot-dicepp"
    assert agent_sync.load_global_skill_names() == [
        "grill-pear",
        "peel-pear",
        "toolchain-review",
    ]
    assert set(agent_sync.collect_global_skills()) == {
        "grill-pear",
        "peel-pear",
        "toolchain-review",
    }


def test_global_skills_can_come_from_any_skill_source_directory(
    agent_sync, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    agent_root = tmp_path / "repo" / "docs" / "agent"
    common = write_skill(agent_root / "skills-common", "common-skill")
    dev = write_skill(agent_root / "skills-dev", "dev-skill")
    write_manifest(agent_root, ["common-skill", "dev-skill"])
    monkeypatch.setattr(agent_sync, "agent_dir", lambda: agent_root)

    sources = agent_sync.collect_global_skills()

    assert sources["common-skill"].path == common
    assert sources["dev-skill"].path == dev


def test_global_skill_source_must_be_unique_across_skill_directories(
    agent_sync, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    agent_root = tmp_path / "repo" / "docs" / "agent"
    write_skill(agent_root / "skills-common", "duplicate")
    write_skill(agent_root / "skills-dev", "duplicate")
    write_manifest(agent_root, ["duplicate"])
    monkeypatch.setattr(agent_sync, "agent_dir", lambda: agent_root)

    with pytest.raises(agent_sync.SyncError, match="multiple sources"):
        agent_sync.collect_global_skills()


def test_global_plan_distinguishes_linked_missing_conflict_and_stale(
    agent_sync, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    agent_root = tmp_path / "repo" / "docs" / "agent"
    common_root = agent_root / "skills-common"
    names = ("linked", "missing", "conflict", "old")
    sources = {name: write_skill(common_root, name) for name in names}
    write_manifest(agent_root, ["linked", "missing", "conflict"])
    monkeypatch.setattr(agent_sync, "agent_dir", lambda: agent_root)

    skills_dir = tmp_path / "home" / ".agents" / "skills"
    create_directory_link(agent_sync, skills_dir / "linked", sources["linked"])
    (skills_dir / "conflict").mkdir()
    create_directory_link(agent_sync, skills_dir / "old", sources["old"])
    target = agent_sync.GlobalTargetSpec("agents", skills_dir, ("codex",))

    plan = agent_sync.build_global_plan([target])

    assert [source.name for _spec, source, _dest in plan.linked] == ["linked"]
    assert [source.name for _spec, source, _dest in plan.missing] == ["missing"]
    assert [source.name for _spec, source, _dest in plan.conflicts] == ["conflict"]
    assert [entry.name for _spec, entry in plan.stale] == ["old"]


def test_project_projection_is_kept_until_global_link_exists(
    agent_sync, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    agent_root = tmp_path / "repo" / "docs" / "agent"
    common_root = agent_root / "skills-common"
    global_source = write_skill(common_root, "global-skill")
    write_skill(common_root, "project-skill")
    (agent_root / "skills-dev").mkdir(parents=True)
    write_manifest(agent_root, ["global-skill"])
    home = tmp_path / "home"
    monkeypatch.setattr(agent_sync, "agent_dir", lambda: agent_root)
    monkeypatch.setattr(agent_sync, "user_home", lambda: home)

    without_link = agent_sync.collect_project_skills("dev", "codex")
    assert set(without_link) == {"global-skill", "project-skill"}

    link = home / ".agents" / "skills" / "global-skill"
    create_directory_link(agent_sync, link, global_source)
    with_link = agent_sync.collect_project_skills("dev", "codex")
    assert set(with_link) == {"project-skill"}


def test_other_checkout_of_same_repository_remains_global_provider(
    agent_sync, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    current_agent_root = tmp_path / "dev" / "docs" / "agent"
    provider_agent_root = tmp_path / "prod" / "docs" / "agent"
    current_source = write_skill(current_agent_root / "skills-dev", "shared")
    provider_source = write_skill(provider_agent_root / "skills-dev", "shared")
    write_manifest(current_agent_root, ["shared"])
    write_manifest(provider_agent_root, ["shared"])
    home = tmp_path / "home"
    skills_dir = home / ".agents" / "skills"
    link = skills_dir / "shared"
    create_directory_link(agent_sync, link, provider_source)
    target = agent_sync.GlobalTargetSpec("agents", skills_dir, ("codex",))
    monkeypatch.setattr(agent_sync, "agent_dir", lambda: current_agent_root)
    monkeypatch.setattr(agent_sync, "user_home", lambda: home)
    monkeypatch.setattr(agent_sync, "global_target_specs", lambda: [target])

    plan = agent_sync.build_global_plan()

    assert not plan.conflicts
    assert [(source.name, provider) for _spec, source, _dest, provider in plan.provided] == [
        ("shared", provider_source.resolve())
    ]
    assert "shared" not in agent_sync.collect_project_skills("dev", "codex")

    agent_sync.apply_global(dry_run=False)
    assert agent_sync.same_target(link, provider_source)

    agent_sync.apply_global(dry_run=False, relink=True)
    assert agent_sync.same_target(link, current_source)


def test_new_skill_waits_for_provider_update_or_explicit_relink(
    agent_sync, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    current_agent_root = tmp_path / "dev" / "docs" / "agent"
    provider_agent_root = tmp_path / "prod" / "docs" / "agent"
    current_shared = write_skill(current_agent_root / "skills-common", "shared")
    current_new = write_skill(current_agent_root / "skills-common", "new")
    provider_shared = write_skill(provider_agent_root / "skills-common", "shared")
    write_manifest(current_agent_root, ["shared", "new"])
    write_manifest(provider_agent_root, ["shared"])
    skills_dir = tmp_path / "home" / ".agents" / "skills"
    shared_link = skills_dir / "shared"
    create_directory_link(agent_sync, shared_link, provider_shared)
    target = agent_sync.GlobalTargetSpec("agents", skills_dir, ("codex",))
    monkeypatch.setattr(agent_sync, "agent_dir", lambda: current_agent_root)
    monkeypatch.setattr(agent_sync, "global_target_specs", lambda: [target])

    plan = agent_sync.build_global_plan()

    assert [(source.name, reason) for _spec, source, _dest, reason in plan.blocked] == [
        ("new", "the provider checkout does not expose this skill")
    ]
    with pytest.raises(agent_sync.SyncError, match="provider checkout"):
        agent_sync.apply_global(dry_run=False)

    agent_sync.apply_global(dry_run=False, relink=True)
    assert agent_sync.same_target(shared_link, current_shared)
    assert agent_sync.same_target(skills_dir / "new", current_new)


def test_global_apply_refuses_to_overwrite_conflict(
    agent_sync, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    agent_root = tmp_path / "repo" / "docs" / "agent"
    write_skill(agent_root / "skills-common", "shared")
    write_manifest(agent_root, ["shared"])
    skills_dir = tmp_path / "home" / ".agents" / "skills"
    conflict = skills_dir / "shared"
    conflict.mkdir(parents=True)
    marker = conflict / "keep.txt"
    marker.write_text("personal", encoding="utf-8")
    target = agent_sync.GlobalTargetSpec("agents", skills_dir, ("codex",))
    monkeypatch.setattr(agent_sync, "agent_dir", lambda: agent_root)
    monkeypatch.setattr(agent_sync, "global_target_specs", lambda: [target])

    with pytest.raises(agent_sync.SyncError, match="Refusing to overwrite"):
        agent_sync.apply_global(dry_run=False)

    assert marker.read_text(encoding="utf-8") == "personal"


def test_global_apply_links_missing_and_removes_recognized_stale_link(
    agent_sync, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    agent_root = tmp_path / "repo" / "docs" / "agent"
    common_root = agent_root / "skills-common"
    desired = write_skill(common_root, "desired")
    old = write_skill(common_root, "old")
    write_manifest(agent_root, ["desired"])
    skills_dir = tmp_path / "home" / ".agents" / "skills"
    stale_link = skills_dir / "old"
    create_directory_link(agent_sync, stale_link, old)
    target = agent_sync.GlobalTargetSpec("agents", skills_dir, ("codex",))
    monkeypatch.setattr(agent_sync, "agent_dir", lambda: agent_root)
    monkeypatch.setattr(agent_sync, "global_target_specs", lambda: [target])

    agent_sync.apply_global(dry_run=False)

    assert agent_sync.same_target(skills_dir / "desired", desired)
    assert not agent_sync.path_entry_exists(stale_link)


def test_global_link_mode_is_platform_specific(agent_sync) -> None:
    assert agent_sync.global_link_mode("nt") == "junction"
    assert agent_sync.global_link_mode("posix") == "symlink"
