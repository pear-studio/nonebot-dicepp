"""Fail-closed Windows matrix boundary during the manual protocol transition.

The old UpdateGuard source protocol is intentionally unsupported.  A real
simplified-protocol harness can replace this boundary only after the first
manual-migration release is public and pinned in ``upgrade_matrix.json``.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

try:
    from scripts.build.upgrade_matrix_platform_harness import run_unavailable
except ModuleNotFoundError:  # Direct ``python scripts/build/...`` execution.
    from upgrade_matrix_platform_harness import run_unavailable


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
        result = run_unavailable("windows", context)
        result["observations"]["reason"] = (
            "Windows simplified-protocol matrix requires a pinned published "
            "source release; manual-migration candidates are validated by the "
            "final package validator instead"
        )
    except Exception as exc:
        result = run_unavailable("windows", context)
        result["observations"]["reason"] = (
            f"Windows matrix context is invalid: {exc}"
        )
    _write_result(args.output, result)
    return 0 if result.get("status") == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
