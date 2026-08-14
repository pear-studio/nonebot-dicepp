from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from dicepp_data import InstanceLayout
from dicepp_manager.upgrade import SimpleWindowsVelopackUpgradeAdapter


@pytest.mark.skipif(os.name != "nt", reason="requires Windows cmd directory moves")
def test_root_recovery_script_swaps_whole_current_without_current_helpers(
    tmp_path: Path,
) -> None:
    layout = InstanceLayout.from_root(tmp_path)
    layout.manager_recovery_dir.mkdir(parents=True)
    (layout.root / "current").mkdir()
    (layout.root / "current" / "old-program.txt").write_text("old")
    (layout.root / "Update.exe").write_bytes(b"updater")
    (layout.root / "DicePP.exe").write_bytes(b"launcher")
    adapter = SimpleWindowsVelopackUpgradeAdapter(
        layout=layout,
        install_command=[
            str(layout.root / "Update.exe"),
            "apply",
            "--waitPid",
            "{wait_pid}",
            "-p",
            "{package}",
        ],
        version_loader=lambda: "3.0.0",
    )
    transaction_id = "d" * 32
    adapter._stage_program_backup(transaction_id)
    recovery = layout.manager_recovery_dir / transaction_id
    adapter._write_recovery_material(
        recovery,
        {
            "format_version": 1,
            "transaction_id": transaction_id,
            "source_version": "3.0.0",
            "target_version": "3.1.0",
            "pre_upgrade_filename": "pre-upgrade.zip",
            "original_running": [],
        },
    )
    shutil.rmtree(layout.root / "current")
    (layout.root / "current").mkdir()
    (layout.root / "current" / "broken-target.txt").write_text("broken")
    blocker_path = layout.root / "current" / "blocking.exe"
    shutil.copyfile(
        Path(os.environ["WINDIR"]) / "System32" / "ping.exe",
        blocker_path,
    )
    shutil.copyfile(
        Path(os.environ["WINDIR"]) / "System32" / "where.exe",
        layout.root / "DicePP.exe",
    )

    blocker = subprocess.Popen(
        [str(blocker_path), "-n", "4", "127.0.0.1"],
        cwd=layout.root / "current",
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        result = subprocess.run(
            [
                os.environ["COMSPEC"],
                "/d",
                "/c",
                str(layout.root / "DicePP-Recover.cmd"),
            ],
            cwd=layout.root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            timeout=20,
            check=False,
        )
    finally:
        if blocker.poll() is None:
            blocker.kill()
        blocker.wait(timeout=5)

    assert result.returncode == 0, result.stdout.decode(errors="replace")
    assert (layout.root / "current" / "old-program.txt").read_text() == "old"
    assert not (layout.root / "current" / "broken-target.txt").exists()
    assert (recovery / "failed-current" / "broken-target.txt").is_file()
    assert (recovery / "manual-restore.requested").is_file()
