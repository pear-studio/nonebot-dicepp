"""Closed platform-harness boundary for cross-version upgrade scenarios.

The tracked Windows/Linux wrappers deliberately fail until their real process
orchestration is implemented.  They still validate that the runner supplied
the pinned historical assets and final candidate bytes, so replacing the
future implementation cannot silently widen the input contract.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_asset(record: Any, *, label: str) -> None:
    expected = {"purpose", "name", "path", "sha256", "size"}
    if label == "target":
        expected = {"filename", "path", "sha256", "size", "platform", "arch", "purpose"}
    if not isinstance(record, dict) or set(record) != expected:
        raise ValueError(f"{label} asset context is invalid")
    path = Path(record["path"])
    if (
        not path.is_absolute()
        or path.is_symlink()
        or not path.is_file()
        or type(record["size"]) is not int
        or path.stat().st_size != record["size"]
        or _sha256(path) != record["sha256"]
    ):
        raise ValueError(f"{label} asset bytes differ from matrix context")


def run_unavailable(platform: str, context: dict[str, Any]) -> dict[str, Any]:
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
    if set(context) != required or context["contract_version"] != 1:
        raise ValueError("upgrade matrix context fields do not match contract v1")
    if context["platform"] != platform:
        raise ValueError("upgrade matrix context platform differs from entrypoint")
    if not isinstance(context["source_assets"], list) or not context["source_assets"]:
        raise ValueError("upgrade matrix source assets are missing")
    if not isinstance(context["target_assets"], list) or not context["target_assets"]:
        raise ValueError("upgrade matrix target assets are missing")
    for asset in context["source_assets"]:
        _validate_asset(asset, label="source")
    for asset in context["target_assets"]:
        _validate_asset(asset, label="target")
    return {
        "contract_version": 1,
        "platform": platform,
        "arch": context["arch"],
        "source_version": context["source_version"],
        "target_version": context["target_version"],
        "scenario": context["scenario"],
        "status": "unavailable",
        "assertions": {},
        "observations": {
            "reason": (
                f"real {platform} cross-version scenario orchestration is not "
                "implemented at this candidate SHA"
            )
        },
    }


def main(platform: str) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--context", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    context = json.loads(args.context.read_text(encoding="utf-8"))
    result = run_unavailable(platform, context)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return 2
