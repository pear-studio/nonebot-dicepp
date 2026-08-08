"""Run and assemble fail-closed cross-version upgrade matrix evidence.

The platform harness is an external executable because Windows Velopack and
Linux Docker need different process orchestration.  For every matrix cell this
runner downloads (or reuses) pinned source bytes, writes a closed JSON context,
and requires the harness to return non-empty passing behavioral assertions.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Any

try:
    from scripts.build.upgrade_evidence import (
        CandidateIdentity,
        FinalAssetIdentity,
        UPGRADE_EVIDENCE_CONTRACT_VERSION,
        candidate_digest,
        load_json_object,
        normalize_candidate_identities,
        normalize_final_asset_identities,
        parse_candidate_identity,
        required_scenarios_for,
        source_scenarios_for,
        validate_upgrade_evidence,
        validate_upgrade_matrix,
        validate_upgrade_matrix_coverage,
        validate_upgrade_matrix_platform_coverage,
        validate_scenario_result,
    )
except ModuleNotFoundError:  # Direct ``python scripts/build/...`` execution.
    from upgrade_evidence import (
        CandidateIdentity,
        FinalAssetIdentity,
        UPGRADE_EVIDENCE_CONTRACT_VERSION,
        candidate_digest,
        load_json_object,
        normalize_candidate_identities,
        normalize_final_asset_identities,
        parse_candidate_identity,
        required_scenarios_for,
        source_scenarios_for,
        validate_upgrade_evidence,
        validate_upgrade_matrix,
        validate_upgrade_matrix_coverage,
        validate_upgrade_matrix_platform_coverage,
        validate_scenario_result,
    )


HARNESS_RESULT_CONTRACT_VERSION = 1
PLATFORM_RESULT_CONTRACT_VERSION = 1
EXPECTED_PLATFORM_TARGET_PURPOSES = {
    "windows": {"portable", "setup", "velopack-bundle"},
    "linux": {"linux-bundle"},
}
HARNESS_ENTRYPOINTS = {
    "windows": Path(__file__).with_name("windows_upgrade_matrix_harness.py"),
    "linux": Path(__file__).with_name("linux_upgrade_matrix_harness.py"),
}

_VERSION_RE = re.compile(
    r"^v?(\d+)\.(\d+)\.(\d+)(?:(?:rc|-rc\.)(\d+)|a(\d+)|b(\d+))?$"
)


def _version_key(value: str) -> tuple[int, int, int, int, int]:
    match = _VERSION_RE.fullmatch(value)
    if match is None:
        raise ValueError(f"unsupported matrix version: {value!r}")
    major, minor, patch = (int(match.group(index)) for index in range(1, 4))
    rc, alpha, beta = match.group(4), match.group(5), match.group(6)
    if alpha is not None:
        suffix = (0, int(alpha))
    elif beta is not None:
        suffix = (1, int(beta))
    elif rc is not None:
        suffix = (2, int(rc))
    else:
        suffix = (3, 0)
    return major, minor, patch, *suffix


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _target_asset(
    spec: str, *, platform: str, arch: str
) -> tuple[FinalAssetIdentity, Path]:
    try:
        purpose, raw_path = spec.split("=", 1)
    except ValueError as exc:
        raise ValueError("target asset must be PURPOSE=PATH") from exc
    path = Path(raw_path).resolve(strict=True)
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"target asset is not a regular file: {path}")
    return (
        FinalAssetIdentity(
            platform=platform,
            arch=arch,
            purpose=purpose,
            filename=path.name,
            size=path.stat().st_size,
            sha256=_sha256(path),
        ),
        path,
    )


def _source_asset(asset: dict[str, str], cache: Path) -> Path:
    cache.mkdir(parents=True, exist_ok=True)
    path = cache / asset["name"]
    if path.exists() and (path.is_symlink() or not path.is_file()):
        raise ValueError(f"source cache entry is not a regular file: {path}")
    if not path.exists():
        partial = cache / f"{asset['name']}.partial"
        urllib.request.urlretrieve(asset["url"], partial)
        if _sha256(partial) != asset["sha256"]:
            partial.unlink(missing_ok=True)
            raise ValueError(
                f"downloaded source asset digest mismatch: {asset['name']}"
            )
        partial.replace(path)
    if _sha256(path) != asset["sha256"]:
        raise ValueError(f"cached source asset digest mismatch: {asset['name']}")
    return path.resolve()


def _run_harness(
    entrypoint: Path, *, context: dict[str, Any], work_dir: Path
) -> tuple[dict[str, Any], subprocess.CompletedProcess[Any]]:
    if entrypoint.is_symlink() or not entrypoint.is_file():
        raise ValueError(f"tracked upgrade harness entrypoint is unavailable: {entrypoint}")
    work_dir.mkdir(parents=True, exist_ok=False)
    work_dir = work_dir.resolve()
    context_path = work_dir / "context.json"
    result_path = work_dir / "result.json"
    context_path.write_text(
        json.dumps(context, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    completed = subprocess.run(
        [
            sys.executable,
            str(entrypoint.resolve()),
            "--context",
            str(context_path),
            "--output",
            str(result_path),
        ],
        cwd=work_dir,
        check=False,
    )
    result = load_json_object(result_path, label="upgrade harness result")
    if set(result) != {
        "contract_version",
        "platform",
        "arch",
        "source_version",
        "target_version",
        "scenario",
        "status",
        "assertions",
        "observations",
    }:
        raise ValueError(
            "upgrade harness result fields do not match contract version 1"
        )
    if (
        result["contract_version"] != HARNESS_RESULT_CONTRACT_VERSION
        or result["platform"] != context["platform"]
        or result["arch"] != context["arch"]
        or result["source_version"] != context["source_version"]
        or result["target_version"] != context["target_version"]
        or result["scenario"] != context["scenario"]
    ):
        raise ValueError("upgrade harness result identity differs from its matrix cell")
    scenario = {
        "name": result["scenario"],
        "status": result["status"],
        "assertions": result["assertions"],
        "observations": result["observations"],
    }
    if completed.returncode == 0:
        scenario = validate_scenario_result(
            scenario,
            expected_platform=context["platform"],
            expected_name=context["scenario"],
            expected_source_version=context["source_version"],
            expected_target_version=context["target_version"],
        )
    return scenario, completed


def run_platform(
    *,
    matrix_path: Path,
    platform: str,
    arch: str,
    target_version: str,
    target_commit_sha: str,
    target_asset_specs: list[str],
    source_cache: Path,
    work_root: Path,
    output: Path,
) -> dict[str, Any]:
    matrix = validate_upgrade_matrix(
        load_json_object(matrix_path, label="upgrade matrix")
    )
    validate_upgrade_matrix_platform_coverage(
        matrix, platform=platform, arch=arch
    )
    sources = [
        source
        for source in matrix["supported_sources"]
        if (source["platform"], source["arch"]) == (platform, arch)
    ]
    if not sources:
        raise ValueError(f"upgrade matrix has no source for {platform}/{arch}")
    target_records = [
        _target_asset(spec, platform=platform, arch=arch)
        for spec in target_asset_specs
    ]
    target_assets = [identity for identity, _path in target_records]
    target_paths = {identity.purpose: path for identity, path in target_records}
    actual_purposes = {item.purpose for item in target_assets}
    if (
        actual_purposes != EXPECTED_PLATFORM_TARGET_PURPOSES.get(platform)
        or len(target_assets) != len(actual_purposes)
    ):
        raise ValueError(f"target assets are incomplete for {platform}/{arch}")
    normalized_target_version = _version_key(target_version)
    for source in sources:
        source_version = _version_key(source["source_version"])
        if source_version >= normalized_target_version:
            raise ValueError(
                "matrix source version must be older than the target candidate"
            )
    try:
        entrypoint = HARNESS_ENTRYPOINTS[platform]
    except KeyError as exc:
        raise ValueError(f"unsupported upgrade harness platform: {platform}") from exc
    results: list[dict[str, Any]] = []
    failed_harness: subprocess.CompletedProcess[Any] | None = None
    for source in sources:
        source_paths = [
            (asset, _source_asset(asset, source_cache / source["source_version"]))
            for asset in source["assets"]
        ]
        scenarios = []
        for scenario in source_scenarios_for(matrix, source):
            context = {
                "contract_version": 1,
                "platform": platform,
                "arch": arch,
                "source_version": source["source_version"],
                "scenario": scenario,
                "source_assets": [
                    {
                        "purpose": asset["purpose"],
                        "name": asset["name"],
                        "path": str(path),
                        "sha256": asset["sha256"],
                        "size": path.stat().st_size,
                    }
                    for asset, path in source_paths
                ],
                "target_version": target_version.removeprefix("v"),
                "target_commit_sha": target_commit_sha,
                "target_assets": [
                    {
                        "platform": item.platform,
                        "arch": item.arch,
                        "purpose": item.purpose,
                        "filename": item.filename,
                        "size": item.size,
                        "sha256": item.sha256,
                        "path": str(target_paths[item.purpose]),
                    }
                    for item in target_assets
                ],
            }
            scenario_dir = work_root / f"{source['source_version']}-{scenario}"
            scenario_result, completed = _run_harness(
                entrypoint, context=context, work_dir=scenario_dir
            )
            scenarios.append(scenario_result)
            if completed.returncode != 0:
                failed_harness = completed
                break
        results.append(
            {
                "platform": platform,
                "arch": arch,
                "source_version": source["source_version"],
                "source_assets": [
                    {
                        "purpose": asset["purpose"],
                        "name": asset["name"],
                        "sha256": asset["sha256"],
                    }
                    for asset in source["assets"]
                ],
                "scenarios": scenarios,
            }
        )
        if failed_harness is not None:
            break
    payload = {
        "contract_version": PLATFORM_RESULT_CONTRACT_VERSION,
        "target": {
            "version": target_version.removeprefix("v"),
            "commit_sha": target_commit_sha,
            "final_assets": [
                {
                    "platform": item.platform,
                    "arch": item.arch,
                    "purpose": item.purpose,
                    "filename": item.filename,
                    "size": item.size,
                    "sha256": item.sha256,
                }
                for item in target_assets
            ],
        },
        "results": results,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if failed_harness is not None:
        raise subprocess.CalledProcessError(
            failed_harness.returncode,
            failed_harness.args,
            output=failed_harness.stdout,
            stderr=failed_harness.stderr,
        )
    return payload


def assemble_evidence(
    *,
    matrix_path: Path,
    target_version: str,
    target_commit_sha: str,
    candidates: list[CandidateIdentity],
    platform_results: list[Path],
    output: Path,
) -> dict[str, Any]:
    matrix = validate_upgrade_matrix(
        load_json_object(matrix_path, label="upgrade matrix")
    )
    validate_upgrade_matrix_coverage(matrix)
    fragments = [
        load_json_object(path, label="platform matrix result")
        for path in platform_results
    ]
    final_assets: list[FinalAssetIdentity] = []
    results: list[dict[str, Any]] = []
    for fragment in fragments:
        if (
            set(fragment) != {"contract_version", "target", "results"}
            or fragment["contract_version"] != 1
        ):
            raise ValueError(
                "platform matrix result fields do not match contract version 1"
            )
        target = fragment["target"]
        if not isinstance(target, dict) or set(target) != {
            "version",
            "commit_sha",
            "final_assets",
        }:
            raise ValueError("platform matrix target is invalid")
        if (
            target["version"] != target_version.removeprefix("v")
            or target["commit_sha"] != target_commit_sha
        ):
            raise ValueError("platform matrix target does not match this candidate")
        try:
            final_assets.extend(
                FinalAssetIdentity(**item) for item in target["final_assets"]
            )
        except (TypeError, AttributeError) as exc:
            raise ValueError("platform matrix final assets are invalid") from exc
        if not isinstance(fragment["results"], list):
            raise ValueError("platform matrix results must be a list")
        results.extend(fragment["results"])
    normalized_candidates = normalize_candidate_identities(candidates)
    normalized_assets = normalize_final_asset_identities(final_assets)
    evidence = {
        "contract_version": UPGRADE_EVIDENCE_CONTRACT_VERSION,
        "target": {
            "version": target_version.removeprefix("v"),
            "commit_sha": target_commit_sha,
            "candidate_identities": normalized_candidates,
            "candidate_digest": candidate_digest(candidates),
            "final_assets": normalized_assets,
        },
        "results": results,
    }
    validate_upgrade_evidence(
        evidence,
        matrix=matrix,
        target_version=target_version,
        target_commit_sha=target_commit_sha,
        target_candidate_identities=candidates,
        target_final_assets=final_assets,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return evidence


def main() -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run-platform")
    run.add_argument("--matrix", type=Path, required=True)
    run.add_argument("--platform", required=True)
    run.add_argument("--arch", required=True)
    run.add_argument("--target-version", required=True)
    run.add_argument("--target-commit-sha", required=True)
    run.add_argument("--target-asset", action="append", default=[])
    run.add_argument("--source-cache", type=Path, required=True)
    run.add_argument("--work-root", type=Path, required=True)
    run.add_argument("--output", type=Path, required=True)
    assemble = commands.add_parser("assemble")
    assemble.add_argument("--matrix", type=Path, required=True)
    assemble.add_argument("--target-version", required=True)
    assemble.add_argument("--target-commit-sha", required=True)
    assemble.add_argument("--candidate", action="append", default=[])
    assemble.add_argument(
        "--platform-result", action="append", type=Path, default=[]
    )
    assemble.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "run-platform":
        run_platform(
            matrix_path=args.matrix,
            platform=args.platform,
            arch=args.arch,
            target_version=args.target_version,
            target_commit_sha=args.target_commit_sha,
            target_asset_specs=args.target_asset,
            source_cache=args.source_cache,
            work_root=args.work_root,
            output=args.output,
        )
    else:
        assemble_evidence(
            matrix_path=args.matrix,
            target_version=args.target_version,
            target_commit_sha=args.target_commit_sha,
            candidates=[parse_candidate_identity(spec) for spec in args.candidate],
            platform_results=args.platform_result,
            output=args.output,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
