"""Command-line entry point for the static test-layout policy checker."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

from tests.support.layout_policy import check_test_layout, render_violations


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Statically check the DicePP test layout and resource boundaries.",
    )
    parser.add_argument(
        "path",
        nargs="?",
        default="tests",
        type=Path,
        help="tests directory or individual Python source to check (default: tests)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    path = args.path.resolve()
    violations = check_test_layout(path)
    if violations:
        print(render_violations(violations, root=path if path.is_dir() else path.parent))
        return 1
    print(f"test layout OK: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
