#!/usr/bin/env python3
"""Discover a repository's test setup and print JSON."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


CONFIG_FILES = [
    "pyproject.toml",
    "pytest.ini",
    "tox.ini",
    "noxfile.py",
    "package.json",
    "vitest.config.ts",
    "vitest.config.js",
    "jest.config.ts",
    "jest.config.js",
    "playwright.config.ts",
    "playwright.config.js",
    "go.mod",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "Makefile",
    "justfile",
    ".gitlab-ci.yml",
]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("repo", nargs="?", default=".")
    parser.add_argument("--out")
    args = parser.parse_args()

    root = Path(args.repo).resolve()
    found_configs = []
    config_hashes = {}
    frameworks: set[str] = set()
    test_commands: list[str] = []

    for rel in CONFIG_FILES:
        path = root / rel
        if path.exists():
            found_configs.append(rel)
            config_hashes[rel] = sha256_file(path)
            text = read_text(path).lower()
            if "pytest" in text:
                frameworks.add("pytest")
            if "vitest" in text:
                frameworks.add("vitest")
            if "jest" in text:
                frameworks.add("jest")
            if "playwright" in text:
                frameworks.add("playwright")
            if "junit" in text:
                frameworks.add("junit")
            if rel == "go.mod":
                frameworks.add("go test")

    for workflow in (root / ".github" / "workflows").glob("*.y*ml") if (root / ".github" / "workflows").exists() else []:
        rel = workflow.relative_to(root).as_posix()
        found_configs.append(rel)
        config_hashes[rel] = sha256_file(workflow)
        text = read_text(workflow).lower()
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("run:") and any(token in stripped for token in ["test", "pytest", "vitest", "jest", "go test"]):
                test_commands.append(stripped[4:].strip())

    test_dirs = [p.relative_to(root).as_posix() for p in root.iterdir() if p.is_dir() and p.name.lower() in {"test", "tests", "__tests__", "spec", "specs"}]

    result = {
        "schema_version": 1,
        "project_root": root.as_posix(),
        "frameworks": sorted(frameworks),
        "config_files": found_configs,
        "config_hashes": config_hashes,
        "test_dirs": test_dirs,
        "ci_test_commands": test_commands,
    }

    data = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(data + "\n", encoding="utf-8")
    else:
        print(data)


if __name__ == "__main__":
    main()
