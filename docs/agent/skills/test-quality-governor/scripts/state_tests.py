#!/usr/bin/env python3
"""Compare and update .temp/test-quality/state.json."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


SCHEMA_VERSION = 1
RUBRIC_VERSION = 1


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def config_hash(discover: dict) -> str:
    h = hashlib.sha256()
    for key, value in sorted(discover.get("config_hashes", {}).items()):
        h.update(key.encode("utf-8"))
        h.update(str(value).encode("utf-8"))
    return h.hexdigest()


def file_hashes(inventory: dict) -> dict[str, str]:
    return {item["path"]: item["hash"] for item in inventory.get("test_files", [])}


def state_path(repo: Path) -> Path:
    return repo / ".temp" / "test-quality" / "state.json"


def compare(args: argparse.Namespace) -> None:
    repo = Path(args.repo).resolve()
    inventory = load_json(Path(args.inventory))
    discover = load_json(Path(args.discover)) if args.discover else {}
    current_files = file_hashes(inventory)
    state = load_json(state_path(repo))
    previous_files = state.get("file_hashes", {})

    changed = [path for path, hash_ in current_files.items() if path in previous_files and previous_files[path] != hash_]
    added = [path for path in current_files if path not in previous_files]
    deleted = [path for path in previous_files if path not in current_files]
    unchanged = [path for path, hash_ in current_files.items() if previous_files.get(path) == hash_]

    stale_reasons = []
    if state.get("schema_version") not in {None, SCHEMA_VERSION}:
        stale_reasons.append("schema_version")
    if state.get("rubric_version") not in {None, RUBRIC_VERSION}:
        stale_reasons.append("rubric_version")
    if discover and state.get("test_config_hash") not in {None, config_hash(discover)}:
        stale_reasons.append("test_config_hash")

    result = {
        "schema_version": SCHEMA_VERSION,
        "rubric_version": RUBRIC_VERSION,
        "state_exists": bool(state),
        "latest_run": state.get("latest_run"),
        "stale_reasons": stale_reasons,
        "unchanged": sorted(unchanged),
        "changed": sorted(changed),
        "added": sorted(added),
        "deleted": sorted(deleted),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


def update(args: argparse.Namespace) -> None:
    repo = Path(args.repo).resolve()
    inventory = load_json(Path(args.inventory))
    discover = load_json(Path(args.discover)) if args.discover else {}
    path = state_path(repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    state = {
        "schema_version": SCHEMA_VERSION,
        "rubric_version": RUBRIC_VERSION,
        "latest_run": args.run_id,
        "project_root": repo.as_posix(),
        "test_config_hash": config_hash(discover) if discover else "",
        "file_hashes": file_hashes(inventory),
    }
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {path}")


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    compare_parser = sub.add_parser("compare")
    compare_parser.add_argument("repo")
    compare_parser.add_argument("--inventory", required=True)
    compare_parser.add_argument("--discover")
    compare_parser.set_defaults(func=compare)

    update_parser = sub.add_parser("update")
    update_parser.add_argument("repo")
    update_parser.add_argument("--inventory", required=True)
    update_parser.add_argument("--discover")
    update_parser.add_argument("--run-id", required=True)
    update_parser.set_defaults(func=update)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
