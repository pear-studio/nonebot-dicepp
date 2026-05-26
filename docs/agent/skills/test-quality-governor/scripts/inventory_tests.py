#!/usr/bin/env python3
"""Build a lightweight inventory of test files and test functions."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
from pathlib import Path


IGNORE_DIRS = {".git", ".venv", "venv", "node_modules", ".temp", ".tox", "dist", "build", "__pycache__"}
TEST_PATTERNS = ("test_*.py", "*_test.py", "*.test.js", "*.test.ts", "*.spec.js", "*.spec.ts", "*_test.go")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def iter_test_files(root: Path):
    for pattern in TEST_PATTERNS:
        for path in root.rglob(pattern):
            if any(part in IGNORE_DIRS for part in path.parts):
                continue
            if path.is_file():
                yield path


def call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = call_name(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    if isinstance(node, ast.Call):
        return call_name(node.func)
    return ""


def parse_py(path: Path) -> list[dict]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
    except SyntaxError:
        return []

    tests: list[dict] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
            markers = []
            signals = []
            for dec in node.decorator_list:
                name = call_name(dec)
                if "pytest.mark." in name:
                    markers.append(name.split("pytest.mark.", 1)[1].split(".", 1)[0])
                if "parametrize" in name:
                    signals.append("parametrize")
                if "given" in name:
                    signals.append("property")
            body_text = ast.get_source_segment(path.read_text(encoding="utf-8", errors="ignore"), node) or ""
            if "mock" in body_text.lower() or "patch(" in body_text:
                signals.append("mock")
            if "snapshot" in body_text.lower():
                signals.append("snapshot")
            if "assert " not in body_text and "pytest.raises" not in body_text:
                signals.append("weak-or-no-assert")
            fixtures = [arg.arg for arg in node.args.args if arg.arg not in {"self", "cls"}]
            tests.append(
                {
                    "name": node.name,
                    "line": node.lineno,
                    "markers": sorted(set(markers)),
                    "fixtures": fixtures,
                    "is_async": isinstance(node, ast.AsyncFunctionDef),
                    "signals": sorted(set(signals)),
                }
            )
    return tests


JS_TEST_RE = re.compile(r"\b(?:it|test)\s*\(\s*['\"]([^'\"]+)['\"]")
GO_TEST_RE = re.compile(r"func\s+(Test\w+)\s*\(")


def parse_text_tests(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    tests: list[dict] = []
    if path.suffix == ".go":
        pattern = GO_TEST_RE
    else:
        pattern = JS_TEST_RE
    for match in pattern.finditer(text):
        line = text.count("\n", 0, match.start()) + 1
        name = match.group(1)
        tests.append({"name": name, "line": line, "markers": [], "fixtures": [], "is_async": False, "signals": []})
    return tests


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("repo", nargs="?", default=".")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    root = Path(args.repo).resolve()
    files = []
    for path in sorted(set(iter_test_files(root))):
        rel = path.relative_to(root).as_posix()
        language = "python" if path.suffix == ".py" else "go" if path.suffix == ".go" else "javascript"
        tests = parse_py(path) if language == "python" else parse_text_tests(path)
        files.append({"path": rel, "hash": sha256_file(path), "language": language, "tests": tests})

    result = {"schema_version": 1, "project_root": root.as_posix(), "test_files": files}
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out} ({len(files)} files)")


if __name__ == "__main__":
    main()
