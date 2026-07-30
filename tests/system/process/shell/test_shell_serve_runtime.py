"""Process-level contract for the long-running DicePP Shell runtime."""

from __future__ import annotations

import json
import os
import http.client
import socket
import subprocess
import sys
import time
import urllib.request
from contextlib import ExitStack
from pathlib import Path

import pytest

from tests.support.processes import format_server_startup_failure, stop_server_process
from tests.support.paths import find_repository_root



PROJECT_ROOT = find_repository_root(Path(__file__))


def _hermetic_env(source: dict[str, str]) -> dict[str, str]:
    """Clone *source* and prepend the current worktree's ``src/`` to PYTHONPATH.

    Shared-worktree setups share a single ``.venv`` whose editable-install
    target is bound to whichever worktree last ran ``uv run``.  Prepending
    ``src/`` guarantees the subprocess loads *this* worktree's code.
    """
    env = source.copy()
    src_dir = str(PROJECT_ROOT / "src")
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = src_dir if not existing else f"{src_dir}{os.pathsep}{existing}"
    return env


def _cli(env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "plugins.DicePP.shell", *args],
        cwd=PROJECT_ROOT,
        env=_hermetic_env(env),
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=True,
        timeout=20,
    )


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_url(url: str, process: subprocess.Popen[str], timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            output = process.stdout.read() if process.stdout else ""
            pytest.fail(f"Process exited before {url} became ready:\n{output}")
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return
        except OSError:
            time.sleep(0.05)
    pytest.fail(f"Timed out waiting for {url}")


def _wait_for_manager(manager: subprocess.Popen[str], root: Path, port: int) -> None:
    """Wait for a token-authenticated Manager API before starting the Bot."""
    token_path = root / "manager" / "state" / "api-token"
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if manager.poll() is not None:
            raise RuntimeError("Manager exited during startup")
        if token_path.is_file():
            token = token_path.read_text(encoding="utf-8").strip()
            request = urllib.request.Request(
                f"http://127.0.0.1:{port}/v1/health",
                headers={"Authorization": f"Bearer {token}"},
            )
            try:
                with urllib.request.urlopen(request, timeout=1) as response:
                    if response.status == 200:
                        return
            except (OSError, http.client.HTTPException):
                pass
        time.sleep(0.05)
    raise TimeoutError(f"Manager did not become ready at http://127.0.0.1:{port}/v1/health")


def _wait_for_runtime_file(
    runtime_path: Path,
    process: subprocess.Popen[str],
) -> None:
    """Wait for Shell to publish its runtime record or explain its early exit."""
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if runtime_path.is_file():
            return
        if process.poll() is not None:
            raise RuntimeError("Shell runtime exited during startup")
        time.sleep(0.05)
    raise TimeoutError(f"Shell runtime did not publish {runtime_path}")


def _startup_failure(
    process: subprocess.Popen[str],
    *,
    log_path: Path,
    name: str,
    url: str,
    started_at: float,
    reason: BaseException,
) -> str:
    """Read a stopped test-owned process log into a startup failure message."""
    return (
        format_server_startup_failure(
            process,
            name=name,
            url=url,
            elapsed_seconds=time.monotonic() - started_at,
            output=log_path.read_text(encoding="utf-8", errors="replace"),
        )
        + f"\nStartup wait error: {reason}"
    )


def test_startup_failure_reads_stopped_process_log(tmp_path: Path) -> None:
    log_path = tmp_path / "startup.log"
    with log_path.open("w+", encoding="utf-8") as log:
        process = subprocess.Popen(
            [sys.executable, "-c", "print('manager startup trace')"],
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
        )
        process.wait(timeout=10)
    message = _startup_failure(
        process,
        log_path=log_path,
        name="Manager e2e server",
        url="http://127.0.0.1:45678/v1/health",
        started_at=time.monotonic() - 15.25,
        reason=TimeoutError("manager did not become ready"),
    )

    assert "Manager e2e server" in message
    assert "127.0.0.1:45678" in message
    assert f"pid={process.pid}" in message
    assert "manager startup trace" in message
    assert "manager did not become ready" in message


@pytest.mark.timeout(60)
def test_serve_routes_cli_messages_and_registers_real_bot_with_manager(
    tmp_path: Path,
):
    registry_root = tmp_path / "shell-registry"
    registry_root.mkdir()
    env = os.environ.copy()
    env["DICEPP_PROJECT_ROOT"] = str(registry_root)
    env["DICEPP_APP_DIR"] = str(registry_root)

    _cli(env, "init", "e2e", "--group", "group-e2e")
    session_dir = registry_root / ".dicepp-shell" / "e2e"
    manager_port = _find_free_port()
    manager_env = env.copy()
    manager_env["DICEPP_PROJECT_ROOT"] = str(session_dir)
    manager_env["DICEPP_APP_DIR"] = str(session_dir)
    manager_env["DICEPP_MANAGER_HOST"] = "127.0.0.1"
    manager_env["DICEPP_MANAGER_PORT"] = str(manager_port)
    manager_env["DICEPP_MANAGER_RUNTIME"] = "unavailable"
    manager_env["DICEPP_MANAGER_RELEASE_SCHEDULER"] = "false"
    env["DICEPP_MANAGER_URL"] = f"http://127.0.0.1:{manager_port}"
    manager: subprocess.Popen[str] | None = None
    process: subprocess.Popen[str] | None = None
    with ExitStack() as exit_stack:
        manager_log = exit_stack.enter_context(
            (tmp_path / "manager-startup.log").open("w+", encoding="utf-8")
        )
        runtime_log = exit_stack.enter_context(
            (tmp_path / "shell-runtime-startup.log").open("w+", encoding="utf-8")
        )
        try:
            manager = subprocess.Popen(
                [sys.executable, "-m", "dicepp_manager"],
                cwd=PROJECT_ROOT,
                env=_hermetic_env(manager_env),
                stdout=manager_log,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
            )
            manager_log.close()
            manager_started_at = time.monotonic()
            manager_url = f"http://127.0.0.1:{manager_port}/v1/health"
            try:
                _wait_for_manager(manager, session_dir, manager_port)
            except (RuntimeError, TimeoutError) as exc:
                stop_server_process(
                    manager,
                    name="Manager e2e server",
                    request_stop=manager.terminate,
                    force_kill_tree=True,
                )
                pytest.fail(
                    _startup_failure(
                        manager,
                        log_path=tmp_path / "manager-startup.log",
                        name="Manager e2e server",
                        url=manager_url,
                        started_at=manager_started_at,
                        reason=exc,
                    )
                )
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "plugins.DicePP.shell",
                    "serve",
                    "e2e",
                    "--port",
                    "0",
                    "--json",
                    "--manager",
                    f"http://127.0.0.1:{manager_port}",
                ],
                cwd=PROJECT_ROOT,
                env=_hermetic_env(env),
                stdout=runtime_log,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            runtime_log.close()
            runtime_path = session_dir / "runtime.json"
            runtime_started_at = time.monotonic()
            try:
                _wait_for_runtime_file(runtime_path, process)
            except (RuntimeError, TimeoutError) as exc:
                stop_server_process(
                    process,
                    name="Shell runtime",
                    request_stop=lambda: _cli(
                        env,
                        "serve",
                        "--stop",
                        "e2e",
                        "--timeout",
                        "10",
                    ),
                    kill_tree=True,
                )
                pytest.fail(
                    _startup_failure(
                        process,
                        log_path=tmp_path / "shell-runtime-startup.log",
                        name="Shell runtime",
                        url=f"http://127.0.0.1:{manager_port}/v1/control/bots",
                        started_at=runtime_started_at,
                        reason=exc,
                    )
                )

            sent = _cli(
                env,
                "send",
                "e2e",
                "--user",
                "player1",
                "--msg",
                ".r 1d20",
                "--dice",
                "20",
                "--json",
            )
            result = json.loads(sent.stdout)
            assert result["dice_consumed"] == 1
            assert "20" in result["text"]
            assert result["raw_command_count"] == 1

            status = json.loads(_cli(env, "serve", "--status", "e2e", "--json").stdout)
            assert status["running"] is True
            assert status["ready"] is True
            assert status["bot_id"] == "shell_e2e"
            assert status["manager_control_enabled"] is True

            # Hermeticity: the serve subprocess must have loaded this worktree's code.
            py_check = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    "import plugins.DicePP.shell.main as m; print(m.__file__)",
                ],
                cwd=PROJECT_ROOT,
                env=_hermetic_env(env),
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=10,
            )
            loaded = Path(py_check.stdout.strip())
            assert str(PROJECT_ROOT) in str(loaded), (
                f"Subprocess loaded shell from {loaded}, expected under {PROJECT_ROOT}"
            )

            token_path = session_dir / "manager" / "state" / "api-token"
            deadline = time.monotonic() + 10
            registered = None
            while time.monotonic() < deadline:
                if token_path.is_file():
                    token = token_path.read_text(encoding="utf-8").strip()
                    request = urllib.request.Request(
                        f"http://127.0.0.1:{manager_port}/v1/control/bots",
                        headers={"Authorization": f"Bearer {token}"},
                    )
                    try:
                        with urllib.request.urlopen(request, timeout=1) as response:
                            rows = json.loads(response.read().decode("utf-8"))["bots"]
                        registered = next(
                            (row for row in rows if row["bot_id"] == "shell_e2e"),
                            None,
                        )
                        if registered is not None and registered["online"]:
                            break
                    except OSError:
                        pass
                time.sleep(0.05)
            assert registered is not None, "Real Shell Bot never registered with Manager"
            assert registered["bot_id"] == "shell_e2e"
            assert registered["version"]

            database = session_dir / "data" / "bots" / "shell_e2e" / "bot_data.db"
            assert database.is_file()
            # Isolation: the bot wrote only into the session workspace, never the
            # source tree's real data dir (DICEPP_PROJECT_ROOT redirects all paths).
            assert not (PROJECT_ROOT / "data" / "bots" / "shell_e2e").exists()

            stopped = _cli(env, "serve", "--stop", "e2e", "--timeout", "10")
            assert "Stopped session 'e2e'" in stopped.stdout
            process.wait(timeout=10)
            assert process.returncode == 0
            assert not runtime_path.exists()
            assert not (session_dir / "runtime.lock").exists()
        finally:
            try:
                if process is not None:
                    stop_server_process(
                        process,
                        name="Shell runtime",
                        request_stop=lambda: _cli(
                            env,
                            "serve",
                            "--stop",
                            "e2e",
                            "--timeout",
                            "10",
                        ),
                        kill_tree=True,
                    )
            finally:
                if manager is not None:
                    stop_server_process(
                        manager,
                        name="Manager e2e server",
                        request_stop=manager.terminate,
                        force_kill_tree=True,
                    )


def _assert_startup_logs_released(tmp_path: Path) -> None:
    """Prove E2E cleanup released Windows file handles before the test returns."""
    for filename in ("manager-startup.log", "shell-runtime-startup.log"):
        path = tmp_path / filename
        released_path = tmp_path / f"released-{filename}"
        assert path.is_file()
        path.replace(released_path)
        released_path.unlink()


@pytest.mark.timeout(60)
def test_manager_health_timeout_preserves_e2e_diagnostics_and_releases_logs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reason = "forced manager health timeout"

    def fail_manager_wait(*_args, **_kwargs) -> None:
        raise TimeoutError(reason)

    monkeypatch.setattr(sys.modules[__name__], "_wait_for_manager", fail_manager_wait)

    with pytest.raises(pytest.fail.Exception) as failed:
        test_serve_routes_cli_messages_and_registers_real_bot_with_manager(tmp_path)

    assert isinstance(failed.value.__context__, TimeoutError)
    assert str(failed.value.__context__) == reason
    message = str(failed.value)
    assert "Manager e2e server" in message
    assert "http://127.0.0.1:" in message
    assert "/v1/health" in message
    assert "pid=" in message
    assert "returncode=" in message
    assert "stdout/stderr:" in message
    assert reason in message
    _assert_startup_logs_released(tmp_path)


@pytest.mark.timeout(60)
def test_runtime_record_timeout_preserves_e2e_diagnostics_and_releases_logs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reason = "forced runtime.json timeout"

    def fail_runtime_wait(*_args, **_kwargs) -> None:
        raise TimeoutError(reason)

    monkeypatch.setattr(
        sys.modules[__name__],
        "_wait_for_runtime_file",
        fail_runtime_wait,
    )

    with pytest.raises(pytest.fail.Exception) as failed:
        test_serve_routes_cli_messages_and_registers_real_bot_with_manager(tmp_path)

    assert isinstance(failed.value.__context__, TimeoutError)
    assert str(failed.value.__context__) == reason
    message = str(failed.value)
    assert "Shell runtime" in message
    assert "http://127.0.0.1:" in message
    assert "/v1/control/bots" in message
    assert "pid=" in message
    assert "returncode=" in message
    assert "stdout/stderr:" in message
    assert reason in message
    _assert_startup_logs_released(tmp_path)
