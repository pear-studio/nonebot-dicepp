"""Windows filesystem proof for the private Manager API token ACL."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

import dicepp_manager.auth as manager_auth


@pytest.mark.skipif(os.name != "nt", reason="Windows ACL contract")
def test_windows_token_acl_removes_inheritance_and_allows_only_trusted_sids(
    tmp_path,
):
    token_path = tmp_path / "manager" / "state" / "api-token"
    token = manager_auth.ensure_api_token(token_path)
    script = r"""
$ErrorActionPreference = 'Stop'
$path = [Environment]::GetEnvironmentVariable('DICEPP_MANAGER_TOKEN_ACL_PATH', 'Process')
$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
Write-Output ("CURRENT|{0}" -f $identity.User.Value)
$acl = [System.IO.File]::GetAccessControl($path)
Write-Output ("OWNER|{0}" -f $acl.GetOwner([System.Security.Principal.SecurityIdentifier]).Value)
Write-Output ("PROTECTED|{0}" -f $acl.AreAccessRulesProtected)
$acl.Access | ForEach-Object {
    $sid = $_.IdentityReference.Translate([System.Security.Principal.SecurityIdentifier]).Value
    Write-Output ("RULE|{0}|{1}|{2}|{3}" -f $sid, [int]$_.FileSystemRights, $_.AccessControlType, $_.IsInherited)
}
""".strip()

    windows_directory = Path(os.environ["SystemRoot"])
    result = subprocess.run(
        [
            str(
                windows_directory
                / "System32"
                / "WindowsPowerShell"
                / "v1.0"
                / "powershell.exe"
            ),
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            script,
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
        env={
            "SystemRoot": str(windows_directory),
            "WINDIR": str(windows_directory),
            "DICEPP_MANAGER_TOKEN_ACL_PATH": str(token_path),
        },
        creationflags=subprocess.CREATE_NO_WINDOW,
    )

    lines = [line for line in result.stdout.splitlines() if line]
    assert token_path.read_text(encoding="utf-8").strip() == token
    assert lines[0].startswith("CURRENT|")
    current_sid = lines[0].split("|", maxsplit=1)[1]
    owner_sid = lines[1].split("|", maxsplit=1)[1]
    assert lines[2] == "PROTECTED|True"
    rule_lines = lines[3:]
    rules = {
        sid: (int(rights), access_type, inherited)
        for _prefix, sid, rights, access_type, inherited in (
            line.split("|", maxsplit=4) for line in rule_lines
        )
    }
    system_sid = "S-1-5-18"
    administrators_sid = "S-1-5-32-544"
    trusted_sids = {current_sid, system_sid, administrators_sid}
    modify_with_synchronize = 0x001301BF
    full_control = 0x001F01FF
    current_mask = (
        full_control
        if current_sid in {system_sid, administrators_sid}
        else modify_with_synchronize
    )
    expected_rules = {
        current_sid: (current_mask, "Allow", "False"),
        system_sid: (full_control, "Allow", "False"),
        administrators_sid: (full_control, "Allow", "False"),
    }

    assert owner_sid in trusted_sids
    assert len(rule_lines) == len(expected_rules)
    assert set(rules) == set(expected_rules)
    assert rules == expected_rules
