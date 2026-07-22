import json

import pytest

from plugins.DicePP.shell.session import SessionRuntimeLease, create_session
from plugins.DicePP.shell import session as session_module


_CRASH_WORKER = """
import sys, json
from pathlib import Path
from plugins.DicePP.shell.session import SessionRuntimeLease
session_dir = Path(sys.argv[1])
result_file = Path(sys.argv[2])
lease = SessionRuntimeLease(session_dir)
lease.acquire()
result_file.write_text(json.dumps({"pid": lease.pid}), encoding="utf-8")
sys.exit(0)
"""


def test_crash_releases_lock(tmp_path, monkeypatch):
    shell_dir = tmp_path / ".dicepp-shell"
    monkeypatch.setattr(session_module, "SHELL_DIR", shell_dir)
    monkeypatch.setattr(session_module, "_LOCKS_DIR", shell_dir / ".locks")
    monkeypatch.setattr(session_module, "_session_lock_path", lambda name: shell_dir / ".locks" / f"{name}.lock")
    session_dir = create_session("crash-test")
    result_file = tmp_path / "crash-result.json"
    import subprocess as process
    import sys
    proc = process.run([sys.executable, "-c", _CRASH_WORKER, str(session_dir), str(result_file)], capture_output=True, text=True, encoding="utf-8", timeout=15)
    assert proc.returncode == 0, proc.stderr
    lease = SessionRuntimeLease(session_dir).acquire()
    lease.release()


_CONCURRENT_WORKER = """
import json, os, sys, time
from pathlib import Path
session_dir = Path(sys.argv[1])
signal_dir = Path(sys.argv[2])
worker_id = sys.argv[3]
monkeypatch_base = Path(sys.argv[4])
import plugins.DicePP.shell.session as _sess
_sess.SHELL_DIR = monkeypatch_base / ".dicepp-shell"
_sess._LOCKS_DIR = _sess.SHELL_DIR / ".locks"
_sess._session_lock_path = lambda name: _sess._LOCKS_DIR / f"{name}.lock"
(signal_dir / f"ready.{worker_id}").write_text("")
go_file = signal_dir / "go"
while not go_file.exists():
    time.sleep(0.005)
from plugins.DicePP.shell.session import RuntimeAlreadyActive, SessionRuntimeLease
result = {"worker": worker_id, "status": "unknown"}
try:
    lease = SessionRuntimeLease(session_dir)
    lease.acquire()
    result["status"] = "acquired"
    time.sleep(1.0)
    lease.release()
except RuntimeAlreadyActive:
    result["status"] = "rejected"
except Exception as exc:
    result["status"] = f"error:{exc!r}"
tmp = signal_dir / f"result.{worker_id}.tmp"
tmp.write_text(json.dumps(result), encoding="utf-8")
os.replace(tmp, signal_dir / f"result.{worker_id}")
"""


def test_concurrent_acquire_grants_single_lease(tmp_path, monkeypatch):
    import subprocess
    import sys
    import time

    monkeypatch_base = tmp_path / "monkeypatch-base"
    monkeypatch_base.mkdir()
    shell_dir = monkeypatch_base / ".dicepp-shell"
    monkeypatch.setattr(session_module, "SHELL_DIR", shell_dir)
    monkeypatch.setattr(session_module, "_LOCKS_DIR", shell_dir / ".locks")
    monkeypatch.setattr(session_module, "_session_lock_path", lambda name: shell_dir / ".locks" / f"{name}.lock")
    session_dir = shell_dir / "race"
    session_dir.mkdir(parents=True)
    signal_dir = tmp_path / "signal"
    signal_dir.mkdir()
    workers = 8
    procs = [subprocess.Popen([sys.executable, "-c", _CONCURRENT_WORKER, str(session_dir), str(signal_dir), str(i), str(monkeypatch_base)]) for i in range(workers)]
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if len(list(signal_dir.glob("ready.*"))) >= workers:
            break
        time.sleep(0.01)
    else:
        for proc in procs:
            proc.kill()
        pytest.fail(f"Only {len(list(signal_dir.glob('ready.*')))}/{workers} workers reported ready")
    (signal_dir / "go").write_text("")
    for proc in procs:
        proc.wait(timeout=30)
    results = [json.loads((signal_dir / f"result.{i}").read_text(encoding="utf-8")) for i in range(workers) if (signal_dir / f"result.{i}").exists()]
    assert len(results) == workers, f"Expected {workers} results, got {len(results)}"
    acquired = [result for result in results if result["status"] == "acquired"]
    rejected = [result for result in results if result["status"] == "rejected"]
    assert len(acquired) == 1, f"Expected 1 acquired, got {acquired}"
    assert len(rejected) == workers - 1, f"Expected {workers - 1} rejected, got {rejected}"
