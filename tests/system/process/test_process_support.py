"""Process lifecycle contracts used by integration-test fixtures."""

from __future__ import annotations

import subprocess
import sys
import warnings

import pytest

from tests.support.processes import stop_server_process


def _start_stdin_test_process(*, exits_on_close: bool) -> subprocess.Popen[str]:
    code = "import sys, time; print('ready', flush=True); "
    code += "sys.stdin.buffer.read()" if exits_on_close else "time.sleep(60)"
    process = subprocess.Popen(
        [sys.executable, "-c", code],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert process.stdout is not None
    assert process.stdout.readline().strip() == "ready"
    return process


def test_stop_server_process_requests_graceful_exit_without_warning() -> None:
    process = _start_stdin_test_process(exits_on_close=True)
    assert process.stdin is not None

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        stop_server_process(
            process,
            name="test server",
            request_stop=process.stdin.close,
            timeout=2,
        )

    assert process.returncode == 0


def test_stop_server_process_warns_before_forcing_unresponsive_process() -> None:
    process = _start_stdin_test_process(exits_on_close=False)
    assert process.stdin is not None

    with pytest.warns(RuntimeWarning, match="等待 0.1 秒后仍未退出.*强制终止"):
        stop_server_process(
            process,
            name="test server",
            request_stop=process.stdin.close,
            timeout=0.1,
        )

    assert process.poll() is not None
