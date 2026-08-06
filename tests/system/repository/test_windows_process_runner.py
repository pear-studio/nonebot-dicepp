from __future__ import annotations

import base64
import json
import os
import subprocess
import time
from pathlib import Path

import psutil
import pytest


pytestmark = pytest.mark.skipif(
    os.name != "nt",
    reason="The versioned runner owns Windows process trees.",
)

ROOT = next(
    parent
    for parent in Path(__file__).resolve().parents
    if (parent / "pyproject.toml").is_file()
)
RUNNER = ROOT / "scripts" / "build" / "windows_process_runner.ps1"


def _encoded(script: str) -> str:
    return base64.b64encode(script.encode("utf-16le")).decode("ascii")


def _ps_literal(path: Path) -> str:
    return str(path).replace("'", "''")


def _run_harness(script: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["pwsh", "-NoProfile", "-EncodedCommand", _encoded(script)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def test_windows_runner_returns_real_stdout_from_a_bounded_process(tmp_path: Path) -> None:
    diagnostics = tmp_path / "diagnostics"
    runner_temp = tmp_path / "runner temp with spaces"
    runner_temp.mkdir()
    child_dir = tmp_path / "child scripts with spaces"
    child_dir.mkdir()
    child_script = child_dir / "echo exact arguments.ps1"
    child_script.write_text(
        """param([string]$Value, [string]$OutputPath)
Start-Sleep -Milliseconds 150
[System.IO.File]::WriteAllText(
    $OutputPath,
    $Value,
    [System.Text.UTF8Encoding]::new($false)
)
[Console]::Out.WriteLine($Value)
""",
        encoding="utf-8",
    )
    output_path = tmp_path / "captured output with spaces.txt"
    expected = 'runner value with spaces, "quotes", and trailing slash\\'
    result = _run_harness(
        f"""
. '{_ps_literal(RUNNER)}'
$env:RUNNER_TEMP = '{_ps_literal(runner_temp)}'
$env:TEMP = $env:RUNNER_TEMP
$env:TMP = $env:RUNNER_TEMP
$output = Invoke-DicePPProcess `
    -FilePath (Get-Command pwsh).Source `
    -Arguments @(
        '-NoProfile',
        '-File',
        '{_ps_literal(child_script)}',
        '-Value',
        '{expected.replace("'", "''")}',
        '-OutputPath',
        '{_ps_literal(output_path)}'
    ) `
    -TimeoutSeconds 5 `
    -Scenario 'contract-success' `
    -DiagnosticsRoot '{_ps_literal(diagnostics)}'
[Console]::Out.WriteLine("RESULT=$output")
"""
    )

    assert result.returncode == 0, result.stderr
    assert f"RESULT={expected}" in result.stdout
    assert output_path.read_text(encoding="utf-8") == expected
    payload = json.loads(
        (diagnostics / "contract-success.success.json").read_text(encoding="utf-8")
    )
    assert payload["reason"] == "success"
    assert payload["exit_code"] == 0
    assert 100 <= payload["duration_ms"] < 5_000


def test_windows_runner_persists_failure_contract_with_both_streams(
    tmp_path: Path,
) -> None:
    diagnostics = tmp_path / "diagnostics"
    child = _encoded(
        """
[Console]::Out.WriteLine("runner-out")
[Console]::Error.WriteLine("runner-err")
Start-Sleep -Milliseconds 150
exit 7
"""
    )
    result = _run_harness(
        f"""
. '{_ps_literal(RUNNER)}'
try {{
    Invoke-DicePPProcess `
        -FilePath (Get-Command pwsh).Source `
        -Arguments @('-NoProfile', '-EncodedCommand', '{child}') `
        -TimeoutSeconds 5 `
        -Scenario 'contract-failure' `
        -DiagnosticsRoot '{_ps_literal(diagnostics)}' | Out-Null
    exit 99
}} catch {{
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 23
}}
"""
    )

    assert result.returncode == 23
    assert "contract-failure" in result.stderr
    assert "exited 7" in result.stderr
    payload = json.loads(
        (diagnostics / "contract-failure.failure.json").read_text(encoding="utf-8")
    )
    assert payload["contract_version"] == 1
    assert payload["scenario"] == "contract-failure"
    assert payload["reason"] == "exit-code"
    assert payload["exit_code"] == 7
    assert 100 <= payload["duration_ms"] < 5_000
    assert "runner-out" in (
        diagnostics / "contract-failure.stdout.txt"
    ).read_text(encoding="utf-8")
    assert "runner-err" in (
        diagnostics / "contract-failure.stderr.txt"
    ).read_text(encoding="utf-8")
    assert (diagnostics / "contract-failure.process-tree.json").is_file()


def test_windows_runner_timeout_terminates_the_owned_descendant_tree(
    tmp_path: Path,
) -> None:
    diagnostics = tmp_path / "diagnostics"
    child_pid_path = tmp_path / "child.pid"
    sleeping_child = _encoded("Start-Sleep -Seconds 60")
    parent = _encoded(
        f"""
$child = Start-Process `
    -FilePath (Get-Command pwsh).Source `
    -ArgumentList @('-NoProfile', '-EncodedCommand', '{sleeping_child}') `
    -PassThru
[System.IO.File]::WriteAllText('{_ps_literal(child_pid_path)}', [string]$child.Id)
Start-Sleep -Seconds 60
"""
    )
    result = _run_harness(
        f"""
. '{_ps_literal(RUNNER)}'
try {{
    Invoke-DicePPProcess `
        -FilePath (Get-Command pwsh).Source `
        -Arguments @('-NoProfile', '-EncodedCommand', '{parent}') `
        -TimeoutSeconds 2 `
        -Scenario 'contract-timeout' `
        -DiagnosticsRoot '{_ps_literal(diagnostics)}' | Out-Null
    exit 99
}} catch {{
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 23
}}
"""
    )

    assert result.returncode == 23
    assert "timed out after 2 seconds" in result.stderr
    assert child_pid_path.is_file()
    child_pid = int(child_pid_path.read_text(encoding="utf-8"))
    try:
        deadline = time.monotonic() + 5
        while psutil.pid_exists(child_pid) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not psutil.pid_exists(child_pid)
    finally:
        if psutil.pid_exists(child_pid):
            psutil.Process(child_pid).kill()
    payload = json.loads(
        (diagnostics / "contract-timeout.failure.json").read_text(encoding="utf-8")
    )
    assert payload["reason"] == "timeout"
    assert payload["process_id"] is not None
    assert 1_500 <= payload["duration_ms"] < 10_000
    assert (diagnostics / "contract-timeout.process-tree.json").is_file()
