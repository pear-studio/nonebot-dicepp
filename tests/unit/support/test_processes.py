from __future__ import annotations

import subprocess

from tests.support import processes


class _Process:
    def __init__(self, *, returncode: int | None = None) -> None:
        self.pid = 4321
        self.returncode = returncode
        self.kill_calls = 0
        self.wait_calls: list[float | None] = []

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        self.wait_calls.append(timeout)
        self.returncode = 0
        return 0

    def kill(self) -> None:
        self.kill_calls += 1
        self.returncode = 1


def _completed_taskkill(command: list[str], **_kwargs) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(command, 0)


def test_force_kill_tree_targets_live_windows_root_before_graceful_stop(
    monkeypatch,
) -> None:
    process = _Process()
    taskkill_calls: list[tuple[list[str], dict[str, object]]] = []
    graceful_stops: list[None] = []

    def taskkill(command: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
        taskkill_calls.append((command, kwargs))
        process.returncode = 1
        return _completed_taskkill(command)

    monkeypatch.setattr(processes.os, "name", "nt")
    monkeypatch.setattr(processes.subprocess, "run", taskkill)
    processes.stop_server_process(
        process,  # type: ignore[arg-type]
        name="test-owned manager",
        request_stop=lambda: graceful_stops.append(None),
        force_kill_tree=True,
    )

    assert taskkill_calls[0][0] == ["taskkill", "/PID", "4321", "/T", "/F"]
    assert graceful_stops == []
    assert process.wait_calls == [10]


def test_force_kill_tree_skips_taskkill_after_parent_already_exited(
    monkeypatch,
) -> None:
    process = _Process(returncode=0)
    taskkill_calls: list[None] = []

    monkeypatch.setattr(processes.os, "name", "nt")
    monkeypatch.setattr(
        processes.subprocess,
        "run",
        lambda *_args, **_kwargs: taskkill_calls.append(None),
    )
    processes.stop_server_process(
        process,  # type: ignore[arg-type]
        name="finished manager",
        request_stop=lambda: None,
        force_kill_tree=True,
    )

    assert taskkill_calls == []
    assert process.wait_calls == [None]


def test_force_kill_tree_keeps_non_windows_graceful_stop_behavior(
    monkeypatch,
) -> None:
    process = _Process()
    graceful_stops: list[None] = []

    monkeypatch.setattr(processes.os, "name", "posix")
    processes.stop_server_process(
        process,  # type: ignore[arg-type]
        name="non-windows server",
        request_stop=lambda: graceful_stops.append(None),
        force_kill_tree=True,
    )

    assert graceful_stops == [None]
    assert process.wait_calls == [10]
    assert process.kill_calls == 0


def test_taskkill_failure_falls_back_to_the_test_owned_root_process(
    monkeypatch,
) -> None:
    process = _Process()

    monkeypatch.setattr(processes.os, "name", "nt")
    monkeypatch.setattr(
        processes.subprocess,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(command, 1),
    )
    processes.stop_server_process(
        process,  # type: ignore[arg-type]
        name="taskkill fallback",
        request_stop=lambda: None,
        force_kill_tree=True,
    )

    assert process.kill_calls == 1
