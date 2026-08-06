"""Tracked Linux cross-version matrix harness entrypoint.

Uses the Docker-based orchestrator to execute real four-scenario cross-version
upgrade tests.  Falls back to ``unavailable`` when prerequisites (Docker,
images, compose) are not met.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

try:
    from scripts.build.upgrade_matrix_platform_harness import run_unavailable
except ModuleNotFoundError:
    from upgrade_matrix_platform_harness import run_unavailable


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--context", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    context = json.loads(args.context.read_text(encoding="utf-8"))

    # Validate the input contract first — always fail-closed on bad input.
    try:
        _validate_context(context, platform="linux")
    except ValueError as exc:
        result = run_unavailable("linux", context)
        result["observations"]["reason"] = f"context validation: {exc}"
        _write_result(args.output, result)
        return 2

    # Try the real Docker orchestrator; fall back to unavailable.
    try:
        try:
            from scripts.build.linux_upgrade_orchestrator import run_linux_scenario
        except ModuleNotFoundError:
            from linux_upgrade_orchestrator import run_linux_scenario

        result = run_linux_scenario(context, args.output.parent)
    except Exception as exc:
        result = run_unavailable("linux", context)
        result["observations"]["reason"] = (
            f"real linux cross-version scenario orchestration failed: {exc}"
        )

    _write_result(args.output, result)
    return 0 if result.get("status") == "passed" else 2


def _validate_context(context: Any, *, platform: str) -> None:
    """Fail-closed: reject a context that does not match the contract."""
    required = {
        "contract_version",
        "platform",
        "arch",
        "source_version",
        "scenario",
        "source_assets",
        "target_version",
        "target_commit_sha",
        "target_assets",
    }
    if not isinstance(context, dict) or set(context) != required:
        raise ValueError("upgrade matrix context fields do not match contract v1")
    if context["contract_version"] != 1:
        raise ValueError("unsupported context contract version")
    if context["platform"] != platform:
        raise ValueError(
            f"context platform {context['platform']!r} differs from {platform}"
        )
    if not isinstance(context["source_assets"], list) or not context["source_assets"]:
        raise ValueError("source assets are missing")
    if not isinstance(context["target_assets"], list) or not context["target_assets"]:
        raise ValueError("target assets are missing")


def _write_result(output: Path, result: dict[str, Any]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    raise SystemExit(main())
