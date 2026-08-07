from __future__ import annotations

import json
import sys
from pathlib import Path

import scripts.build.windows_upgrade_matrix_harness as harness


def test_windows_matrix_writes_real_orchestrator_result(
    monkeypatch,
    tmp_path: Path,
) -> None:
    context = {
        "contract_version": 1,
        "platform": "windows",
        "arch": "amd64",
        "source_version": "3.0.0rc20",
        "scenario": "healthy_commit",
        "source_assets": [{"purpose": "portable"}],
        "target_version": "3.0.0rc21",
        "target_commit_sha": "1" * 40,
        "target_assets": [{"purpose": "portable"}],
    }
    context_path = tmp_path / "context.json"
    output_path = tmp_path / "result.json"
    context_path.write_text(json.dumps(context), encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "windows_upgrade_matrix_harness.py",
            "--context",
            str(context_path),
            "--output",
            str(output_path),
        ],
    )

    expected = {
        "status": "passed",
        "assertions": {"source_started": True},
        "observations": {"source_version_before": "3.0.0rc20"},
    }
    monkeypatch.setattr(harness, "run_windows_scenario", lambda *_: expected)

    assert harness.main() == 0
    result = json.loads(output_path.read_text(encoding="utf-8"))
    assert result == expected


def test_windows_matrix_fails_closed_when_real_orchestrator_raises(
    monkeypatch,
    tmp_path: Path,
) -> None:
    context = {
        "contract_version": 1,
        "platform": "windows",
        "arch": "amd64",
        "source_version": "3.0.0rc20",
        "scenario": "healthy_commit",
        "source_assets": [{"purpose": "portable"}],
        "target_version": "3.0.0rc21",
        "target_commit_sha": "1" * 40,
        "target_assets": [{"purpose": "portable"}],
    }
    context_path = tmp_path / "context.json"
    output_path = tmp_path / "result.json"
    context_path.write_text(json.dumps(context), encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "windows_upgrade_matrix_harness.py",
            "--context",
            str(context_path),
            "--output",
            str(output_path),
        ],
    )

    def fail(*_args):
        raise RuntimeError("boom")

    monkeypatch.setattr(harness, "run_windows_scenario", fail)
    monkeypatch.setattr(
        harness,
        "run_unavailable",
        lambda platform, supplied: {
            "contract_version": 1,
            "platform": platform,
            "arch": supplied["arch"],
            "source_version": supplied["source_version"],
            "target_version": supplied["target_version"],
            "scenario": supplied["scenario"],
            "status": "unavailable",
            "assertions": {},
            "observations": {"reason": "unavailable"},
        },
    )

    assert harness.main() == 2
    result = json.loads(output_path.read_text(encoding="utf-8"))
    assert result["status"] == "unavailable"
    assert result["assertions"] == {}
    assert result["observations"]["reason"].endswith("boom")
