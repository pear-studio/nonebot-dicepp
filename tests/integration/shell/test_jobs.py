"""Persistence contracts for DicePP Shell runtime jobs."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from shell.jobs import RuntimeJobManager


class _WaitingRunner:
    tick = False

    def __init__(self, session_dir):
        self.session_dir = session_dir

    async def warp(self, **_kwargs):
        await asyncio.Event().wait()


def test_running_job_from_previous_runtime_is_marked_interrupted(tmp_path):
    jobs_dir = tmp_path / "session" / "jobs"
    jobs_dir.mkdir(parents=True)
    job_path = jobs_dir / "warp_previous.json"
    job_path.write_text(
        json.dumps({
            "id": "warp_previous",
            "type": "warp",
            "status": "running",
            "request": {"days": 2, "start": None, "dry_run": False},
            "progress": {"day": 1, "days": 2},
            "result": None,
            "error": None,
        }),
        encoding="utf-8",
    )

    manager = RuntimeJobManager(SimpleNamespace(session_dir=tmp_path / "session"))

    recovered = manager.get_job("warp_previous")
    assert recovered["status"] == "interrupted"
    assert recovered["error"] == "Runtime exited before the job completed"
    assert json.loads(job_path.read_text(encoding="utf-8"))["status"] == "interrupted"


@pytest.mark.asyncio
async def test_cancel_before_warp_task_starts_releases_runtime(tmp_path):
    manager = RuntimeJobManager(_WaitingRunner(tmp_path / "session"))

    submitted = await manager.submit_warp(days=1, start=None, dry_run=False)
    assert submitted["progress"] == {
        "hours_advanced": 0,
        "total_hours": 24,
        "minutes_advanced": 0,
        "total_minutes": 1440,
    }
    cancelled = await manager.cancel_job(submitted["id"])

    assert cancelled["status"] == "cancelled"
    assert manager.mode == "idle"
    assert manager.active_job is None

    next_job = await manager.submit_warp(days=1, start=None, dry_run=False)
    assert next_job["status"] == "queued"
    await manager.shutdown()


@pytest.mark.asyncio
async def test_shutdown_before_warp_task_starts_releases_runtime(tmp_path):
    manager = RuntimeJobManager(_WaitingRunner(tmp_path / "session"))
    submitted = await manager.submit_warp(days=1, start=None, dry_run=False)

    await manager.shutdown()

    assert manager.get_job(submitted["id"])["status"] == "cancelled"
    assert manager.mode == "idle"
    assert manager.active_job is None


@pytest.mark.asyncio
async def test_queued_persist_failure_does_not_claim_runtime(tmp_path, monkeypatch):
    manager = RuntimeJobManager(_WaitingRunner(tmp_path / "session"))

    def fail_persist(_job):
        raise OSError("disk unavailable")

    monkeypatch.setattr(manager, "_persist", fail_persist)

    with pytest.raises(OSError, match="disk unavailable"):
        await manager.submit_warp(days=1, start=None, dry_run=False)

    assert manager.mode == "idle"
    assert manager.active_job is None


@pytest.mark.asyncio
async def test_running_persist_failure_releases_runtime(tmp_path, monkeypatch):
    manager = RuntimeJobManager(_WaitingRunner(tmp_path / "session"))
    persist = manager._persist
    call_count = 0

    def fail_second_persist(job):
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise OSError("disk unavailable")
        persist(job)

    monkeypatch.setattr(manager, "_persist", fail_second_persist)

    submitted = await manager.submit_warp(days=1, start=None, dry_run=False)
    for _ in range(3):
        await asyncio.sleep(0)

    failed = manager.get_job(submitted["id"])
    assert failed["status"] == "failed"
    assert failed["error"] == "OSError: disk unavailable"
    assert manager.mode == "idle"
    assert manager.active_job is None


@pytest.mark.asyncio
async def test_cancel_persist_failure_still_cancels_and_releases_runtime(
    tmp_path,
    monkeypatch,
):
    manager = RuntimeJobManager(_WaitingRunner(tmp_path / "session"))
    submitted = await manager.submit_warp(days=1, start=None, dry_run=False)
    await asyncio.sleep(0)
    persist = manager._persist
    failed_once = False

    def fail_once(job):
        nonlocal failed_once
        if not failed_once:
            failed_once = True
            raise OSError("disk unavailable")
        persist(job)

    monkeypatch.setattr(manager, "_persist", fail_once)

    cancelled = await manager.cancel_job(submitted["id"])

    assert cancelled["status"] == "cancelled"
    assert manager.mode == "idle"
    assert manager.active_job is None
