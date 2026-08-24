from __future__ import annotations

import os
import sys
import textwrap
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from dashboard.src.bot_process import BotProcessController


def _command(script: Path) -> list[str]:
    return [sys.executable, "-u", str(script)]


def _write_script(path: Path, body: str) -> None:
    path.write_text(textwrap.dedent(body), encoding="utf-8")


def _wait_for_status(
    controller: BotProcessController,
    predicate,
    *,
    timeout: float = 3.0,
):
    deadline = time.monotonic() + timeout
    status = controller.status()
    while time.monotonic() < deadline:
        if predicate(status):
            return status
        time.sleep(0.02)
        status = controller.status()
    raise AssertionError(f"Bot process did not reach expected state: {status!r}")


def test_single_bot_lifecycle_and_tail_logs(tmp_path: Path) -> None:
    script = tmp_path / "bot.py"
    _write_script(
        script,
        """
        import sys
        import time

        print("bot stdout", flush=True)
        print("bot stderr", file=sys.stderr, flush=True)
        while True:
            time.sleep(0.05)
        """,
    )
    controller = BotProcessController(
        command=_command(script),
        cwd=tmp_path,
        env={"DICEPP_TEST_BOT": "injected"},
        log_path=tmp_path / "logs" / "bot.log",
    )

    try:
        started = controller.start()
        assert started.running
        assert started.pid is not None
        assert controller.status().pid == started.pid

        deadline = time.monotonic() + 3
        logs = ""
        while time.monotonic() < deadline:
            logs = controller.tail_logs(10)
            if "bot stdout" in logs and "bot stderr" in logs:
                break
            time.sleep(0.02)
        assert "bot stdout" in logs
        assert "bot stderr" in logs

        restarted = controller.restart()
        assert restarted.running
        assert restarted.pid is not None
        assert restarted.pid != started.pid

        stopped = controller.stop()
        assert stopped.state == "stopped"
        assert controller.status().state == "stopped"
    finally:
        controller.shutdown()


def test_single_bot_start_is_serialized_and_natural_exit_is_reported(
    tmp_path: Path,
) -> None:
    script = tmp_path / "bot.py"
    _write_script(
        script,
        """
        import os
        import sys
        import time

        print(os.environ["DICEPP_TEST_BOT"], flush=True)
        time.sleep(1)
        sys.exit(7)
        """,
    )
    controller = BotProcessController(
        command=_command(script),
        cwd=tmp_path,
        env={"DICEPP_TEST_BOT": "env-value"},
        log_path=tmp_path / "bot.log",
    )

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            statuses = list(executor.map(lambda _index: controller.start(), range(2)))

        assert len({status.pid for status in statuses}) == 1
        exited = _wait_for_status(
            controller,
            lambda status: status.state == "stopped",
        )
        assert exited.returncode == 7
        assert controller.tail_logs(1) == "env-value"
        assert controller.shutdown().state == "stopped"
    finally:
        controller.shutdown()


@pytest.mark.skipif(os.name == "nt", reason="POSIX child can ignore SIGTERM")
def test_single_bot_stop_kills_a_child_that_ignores_terminate(tmp_path: Path) -> None:
    script = tmp_path / "stubborn_bot.py"
    _write_script(
        script,
        """
        import signal
        import time

        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        print("ready", flush=True)
        while True:
            time.sleep(0.05)
        """,
    )
    controller = BotProcessController(
        command=_command(script),
        cwd=tmp_path,
        env=None,
        log_path=tmp_path / "bot.log",
        stop_timeout=0.05,
    )

    try:
        controller.start()
        _wait_for_status(controller, lambda status: status.running)
        stopped = controller.stop()
        assert stopped.state == "stopped"
        assert stopped.returncode is not None
        assert controller.status().state == "stopped"
    finally:
        controller.shutdown()
