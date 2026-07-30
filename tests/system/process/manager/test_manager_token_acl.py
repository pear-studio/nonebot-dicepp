"""Windows filesystem proof for the private Manager API token ACL."""

from __future__ import annotations

import os
import subprocess

import pytest

import dicepp_manager.auth as manager_auth


@pytest.mark.skipif(os.name != "nt", reason="Windows ACL contract")
def test_windows_token_acl_allows_only_current_user_system_and_administrators(
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
    Write-Output ("RULE|{0}|{1}|{2}|{3}" -f $sid, $_.FileSystemRights, $_.AccessControlType, $_.IsInherited)
}
""".strip()

    result = subprocess.run(
        [
            str(manager_auth._windows_powershell_path()),
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            script,
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=manager_auth._WINDOWS_ACL_TIMEOUT_SECONDS,
        env=manager_auth._windows_acl_environment(token_path),
        creationflags=subprocess.CREATE_NO_WINDOW,
    )

    lines = [line for line in result.stdout.splitlines() if line]
    assert token_path.read_text(encoding="utf-8").strip() == token
    assert lines[0].startswith("CURRENT|")
    current_sid = lines[0].split("|", maxsplit=1)[1]
    assert lines[1] == f"OWNER|{current_sid}"
    assert lines[2] == "PROTECTED|True"
    rules = {
        sid: (rights, access_type, inherited)
        for _prefix, sid, rights, access_type, inherited in (
            line.split("|", maxsplit=4) for line in lines[3:]
        )
    }
    assert set(rules) == {current_sid, "S-1-5-18", "S-1-5-32-544"}
    assert rules["S-1-5-18"] == ("FullControl", "Allow", "False")
    assert rules["S-1-5-32-544"] == ("FullControl", "Allow", "False")
    assert "Modify" in rules[current_sid][0]
    assert rules[current_sid][1:] == ("Allow", "False")
