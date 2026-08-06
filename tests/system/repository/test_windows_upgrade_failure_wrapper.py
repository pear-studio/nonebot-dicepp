from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.build.windows_upgrade_orchestrator import _WindowsUpgradeOrchestrator


pytestmark = [
    pytest.mark.skipif(os.name != "nt", reason="Windows command boundary only"),
    pytest.mark.timeout(30),
]


def test_failure_wrapper_records_real_exit_codes_for_both_branches(
    tmp_path: Path,
) -> None:
    work = tmp_path / "work"
    root = work / "instance"
    work.mkdir()
    root.mkdir()
    fake_update = work / "fake-update.cmd"
    fake_update.write_text(
        "@echo off\r\n"
        "echo %* | findstr /I /C:\"blocked-velopack-root\" >nul\r\n"
        "if %errorlevel% equ 0 exit /b 23\r\n"
        "exit /b 0\r\n",
        encoding="utf-8",
    )
    orchestrator = _WindowsUpgradeOrchestrator(
        source_portable=work / "source-portable.zip",
        source_bundle=work / "source-bundle.zip",
        source_version="3.0.0rc19",
        target_portable=work / "target-portable.zip",
        target_bundle=work / "target-bundle.zip",
        target_version="3.1.0",
        work_dir=work,
        apply_failure=True,
    )
    orchestrator._write_failure_wrapper()
    script = work / "velopack_apply_failure.py"
    target = root / "manager" / "packages" / "3.1.0" / "payload.nupkg"
    rollback = root / "manager" / "state" / "update-guard" / "tx" / "source.nupkg"
    target.parent.mkdir(parents=True)
    rollback.parent.mkdir(parents=True)
    target.write_bytes(b"target")
    rollback.write_bytes(b"source")

    def invoke(package: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(script),
                "--update-exe",
                str(fake_update),
                "--root",
                str(root),
                "--target-version",
                "3.1.0",
                "--event-log",
                str(orchestrator.wrapper_events),
                "--package",
                str(package),
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )

    target_result = invoke(target)
    rollback_result = invoke(rollback)

    assert target_result.returncode == 23
    assert rollback_result.returncode == 0
    events = [
        json.loads(line)
        for line in orchestrator.wrapper_events.read_text(encoding="utf-8").splitlines()
    ]
    assert [(event["branch"], event["exit_code"]) for event in events] == [
        ("target", 23),
        ("rollback", 0),
    ]
    assert all(event["completed_at_ns"] >= event["started_at_ns"] for event in events)
