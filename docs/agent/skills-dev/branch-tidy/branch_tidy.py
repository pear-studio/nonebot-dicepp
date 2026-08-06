#!/usr/bin/env python3
"""Mechanical Git helpers for the branch-tidy workflow.

The script deliberately knows nothing about split, merge, or reorder/reword
passes.  ``start`` creates a recoverable working branch; ``finish`` verifies
Git invariants and atomically replaces the original branch.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


class ToolError(RuntimeError):
    pass


WORK_BRANCH_RE = re.compile(
    r"^branch-tidy/(?P<stem>[a-z0-9][a-z0-9-]*-[0-9a-f]{8}-[0-9]{3,})-work$"
)


def say(message: str) -> None:
    print(f"[branch-tidy] {message}")


def fail(message: str) -> None:
    raise ToolError(message)


def run_git(repo: Path, *args: str, check: bool = True) -> bytes:
    """Run Git without a shell and without opening console windows on Windows."""

    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    process = subprocess.run(
        ["git", *args],
        cwd=str(repo),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        creationflags=flags,
    )
    if check and process.returncode:
        detail = process.stderr.decode("utf-8", "replace").strip()
        fail(f"git {' '.join(args)} 失败: {detail or process.returncode}")
    return process.stdout


def text(data: bytes) -> str:
    return data.decode("utf-8", "replace").strip()


def repo_root(cwd: Path | None = None) -> Path:
    base = (cwd or Path.cwd()).resolve()
    root = Path(text(run_git(base, "rev-parse", "--show-toplevel"))).resolve()
    say(f"仓库路径: {root}")
    return root


def current_branch(repo: Path) -> str:
    branch = text(run_git(repo, "symbolic-ref", "--quiet", "--short", "HEAD", check=False))
    if not branch:
        fail("当前处于 detached HEAD")
    say(f"当前分支: {branch}")
    return branch


def resolve_commit(repo: Path, ref: str) -> str:
    return text(run_git(repo, "rev-parse", "--verify", f"{ref}^{{commit}}"))


def branch_head(repo: Path, branch: str) -> str:
    return resolve_commit(repo, f"refs/heads/{branch}")


def commit_tree(repo: Path, commit: str) -> str:
    return text(run_git(repo, "show", "-s", "--format=%T", commit))


def is_ancestor(repo: Path, older: str, newer: str) -> bool:
    merge_base = text(run_git(repo, "merge-base", older, newer, check=False))
    return merge_base == older


def clean_worktree(repo: Path) -> bool:
    return not text(run_git(repo, "status", "--porcelain", "--untracked-files=all"))


def require_clean(repo: Path) -> None:
    if not clean_worktree(repo):
        fail("工作区、index 或未跟踪文件不干净")
    say("工作区: clean")


def git_common_dir(repo: Path) -> Path:
    value = Path(text(run_git(repo, "rev-parse", "--git-common-dir")))
    if not value.is_absolute():
        value = repo / value
    return value.resolve()


def ref_exists(repo: Path, ref: str) -> bool:
    return bool(text(run_git(repo, "show-ref", "--verify", "--hash", ref, check=False)))


def branch_slug(branch: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", branch.lower()).strip("-") or "branch"


def parse_range(repo: Path, spec: str) -> tuple[str, str]:
    if spec.count("..") != 1 or "..." in spec:
        fail("--range 必须使用 Git 的 LEFT..RIGHT 语义")
    left_spec, right_spec = spec.split("..", 1)
    if not left_spec or not right_spec:
        fail("--range 的两端不能为空")
    base = resolve_commit(repo, left_spec)
    right = resolve_commit(repo, right_spec)
    if not is_ancestor(repo, base, right):
        fail(f"LEFT 不是 RIGHT 的祖先: {base}..{right}")
    merges = text(run_git(repo, "rev-list", "--merges", f"{base}..{right}"))
    if merges:
        fail("范围包含 merge commit；branch-tidy 要求线性历史")
    say(f"整理范围: {base}（不含）..{right}（包含）")
    return base, right


def branch_names(repo: Path) -> list[str]:
    output = text(
        run_git(repo, "for-each-ref", "--format=%(refname:short)", "refs/heads/branch-tidy")
    )
    return output.splitlines() if output else []


def next_sequence(repo: Path, prefix: str) -> int:
    pattern = re.compile(rf"^{re.escape(prefix)}(?P<number>[0-9]+)-(?:backup|work)$")
    numbers = [
        int(match.group("number"))
        for branch in branch_names(repo)
        if (match := pattern.fullmatch(branch))
    ]
    return max(numbers, default=0) + 1


def write_manifest(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_manifest(path: Path) -> dict[str, Any]:
    if not path.is_file():
        fail(f"manifest 不存在: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"无法读取 manifest {path}: {exc}")
    if not isinstance(data, dict):
        fail(f"manifest 格式错误: {path}")
    required = {"version", "created_at", "base", "target_branch", "old_head"}
    missing = sorted(required - data.keys())
    if missing:
        fail(f"manifest 缺少字段: {', '.join(missing)}")
    if data["version"] != 2:
        fail(f"manifest 版本不受支持: {data['version']!r}")
    for field in ("base", "target_branch", "old_head"):
        if not isinstance(data[field], str) or not data[field]:
            fail(f"manifest 字段无效: {field}")
    return data


def manifest_for_stem(repo: Path, stem: str) -> Path:
    return git_common_dir(repo) / "branch-tidy" / f"{stem}.json"


def start(args: argparse.Namespace) -> int:
    repo = repo_root(Path(args.cwd) if args.cwd else None)
    target = current_branch(repo)
    if target.startswith("branch-tidy/") and target.endswith("-work"):
        fail("当前已经位于 branch-tidy work 分支；请先完成或手动切离本轮整理")
    require_clean(repo)

    base, right = parse_range(repo, args.range_spec)
    old_head = branch_head(repo, target)
    if not is_ancestor(repo, base, old_head):
        fail("范围 LEFT 不是当前分支 HEAD 的祖先")
    if text(run_git(repo, "rev-list", "--merges", f"{base}..{old_head}")):
        fail("范围 LEFT 到当前分支 HEAD 包含 merge commit")

    target_slug = branch_slug(target)
    prefix = f"branch-tidy/{target_slug}-{base[:8]}-"
    sequence = next_sequence(repo, prefix)
    number = f"{sequence:03d}"
    stem = f"{target_slug}-{base[:8]}-{number}"
    backup_branch = f"branch-tidy/{stem}-backup"
    work_branch = f"branch-tidy/{stem}-work"
    manifest = manifest_for_stem(repo, stem)

    for branch in (backup_branch, work_branch):
        if ref_exists(repo, f"refs/heads/{branch}"):
            fail(f"目标分支已存在，拒绝覆盖: {branch}")
    if manifest.exists():
        fail(f"目标 manifest 已存在，拒绝覆盖: {manifest}")

    say(f"目标分支: {target} -> {old_head}")
    say(f"范围 RIGHT: {right}")
    say(f"本轮序号: {number}")
    say(f"创建 backup 分支: {backup_branch} -> {old_head}")
    run_git(repo, "branch", backup_branch, old_head)
    say(f"创建 work 分支: {work_branch} -> {old_head}")
    run_git(repo, "branch", work_branch, old_head)

    data = {
        "version": 2,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "base": base,
        "target_branch": target,
        "old_head": old_head,
    }
    write_manifest(manifest, data)
    say(f"写入临时 manifest: {manifest}")
    say(f"切换到 work 分支: {work_branch}")
    run_git(repo, "switch", work_branch)
    say("start 完成；请按技能约束在当前分支整理提交")
    say(f"完成后运行: python {Path(__file__).resolve()} finish")
    return 0


def finish(args: argparse.Namespace) -> int:
    repo = repo_root(Path(args.cwd) if args.cwd else None)
    work_branch = current_branch(repo)
    match = WORK_BRANCH_RE.fullmatch(work_branch)
    if not match:
        fail("finish 必须从 branch-tidy/*-work 分支运行")
    require_clean(repo)

    stem = match.group("stem")
    backup_branch = f"branch-tidy/{stem}-backup"
    manifest = manifest_for_stem(repo, stem)
    say(f"backup 分支: {backup_branch}")
    say(f"临时 manifest: {manifest}")
    data = read_manifest(manifest)

    base = resolve_commit(repo, str(data["base"]))
    old_head = resolve_commit(repo, str(data["old_head"]))
    target = str(data["target_branch"])
    expected_stem_prefix = f"{branch_slug(target)}-{base[:8]}-"
    if not stem.startswith(expected_stem_prefix):
        fail("当前 work 分支名称与 manifest 不匹配")
    if not ref_exists(repo, f"refs/heads/{backup_branch}"):
        fail(f"backup 分支不存在: {backup_branch}")
    if not ref_exists(repo, f"refs/heads/{target}"):
        fail(f"原分支不存在: {target}")

    backup_head = branch_head(repo, backup_branch)
    target_head = branch_head(repo, target)
    candidate_head = branch_head(repo, work_branch)
    say(f"原分支: {target} -> {target_head}")
    say(f"backup HEAD: {backup_head}")
    say(f"候选 HEAD: {candidate_head}")
    if backup_head != old_head:
        fail(f"backup 分支已移动: {backup_head} != {old_head}")
    if target_head != old_head:
        fail(f"原分支已移动: {target_head} != {old_head}")
    if not is_ancestor(repo, base, candidate_head):
        fail("范围 LEFT 不是候选 HEAD 的祖先")
    merges = text(run_git(repo, "rev-list", "--merges", f"{base}..{candidate_head}"))
    if merges:
        fail("候选范围包含 merge commit")

    original_tree = commit_tree(repo, backup_head)
    candidate_tree = commit_tree(repo, candidate_head)
    say(f"原最终 tree: {original_tree}")
    say(f"候选最终 tree: {candidate_tree}")
    if candidate_tree != original_tree:
        fail("候选最终 tree 与整理前不一致")

    say(f"原子更新原分支: {target} {old_head} -> {candidate_head}")
    run_git(repo, "update-ref", f"refs/heads/{target}", candidate_head, old_head)
    say(f"切回原分支: {target}")
    run_git(repo, "switch", target)

    if branch_head(repo, target) != candidate_head or resolve_commit(repo, "HEAD") != candidate_head:
        fail("切回后原分支 HEAD 与已验证候选不一致")
    require_clean(repo)
    if branch_head(repo, backup_branch) != old_head:
        fail("替换后 backup 分支发生移动")

    say(f"安全删除 work 分支: {work_branch}")
    run_git(repo, "branch", "-d", work_branch)
    try:
        manifest.unlink()
    except OSError as exc:
        fail(f"原分支已替换且 work 已删除，但 manifest 删除失败: {exc}")
    say(f"删除临时 manifest: {manifest}")
    say(f"finish 完成；backup 保留: {backup_branch}")
    say("未创建 merge commit，未 push，未删除 backup")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    start_parser = subparsers.add_parser("start", help="创建 backup/work 分支并切到 work")
    start_parser.add_argument(
        "--range", dest="range_spec", required=True, help="Git 原生 LEFT..RIGHT 范围"
    )
    start_parser.add_argument("--cwd", help=argparse.SUPPRESS)
    start_parser.set_defaults(handler=start)

    finish_parser = subparsers.add_parser("finish", help="检查候选并替换原分支")
    finish_parser.add_argument("--cwd", help=argparse.SUPPRESS)
    finish_parser.set_defaults(handler=finish)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        return int(args.handler(args))
    except ToolError as exc:
        print(f"[branch-tidy] 失败: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("[branch-tidy] 已中止，未执行额外清理", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
