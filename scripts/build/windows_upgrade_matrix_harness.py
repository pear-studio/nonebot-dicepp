"""Tracked Windows cross-version matrix harness entrypoint.

The entrypoint is intentionally small.  All process and Velopack orchestration
lives in :mod:`windows_upgrade_orchestrator`; malformed inputs and missing
Windows prerequisites are reported as ``unavailable`` and therefore cannot be
assembled into release evidence.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

try:
    from scripts.build.upgrade_matrix_platform_harness import run_unavailable
    from scripts.build.windows_upgrade_orchestrator import run_windows_scenario
except ModuleNotFoundError:  # Direct ``python scripts/build/...`` execution.
    from upgrade_matrix_platform_harness import run_unavailable
    from windows_upgrade_orchestrator import run_windows_scenario


def _validate_context(context: Any) -> None:
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
    if context["contract_version"] != 1 or context["platform"] != "windows":
        raise ValueError("Windows harness received another context contract")
    if context["arch"] != "amd64":
        raise ValueError("Windows harness only supports amd64")
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--context", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    context = json.loads(args.context.read_text(encoding="utf-8"))
    try:
        _validate_context(context)
        result = run_windows_scenario(context, args.output.parent)
    except Exception as exc:
        result = run_unavailable("windows", context)
        result["observations"]["reason"] = (
            f"real Windows cross-version scenario orchestration failed: {exc}"
        )
    _write_result(args.output, result)
    return 0 if result.get("status") == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
