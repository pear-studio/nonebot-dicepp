"""Persistent background jobs owned by one DicePP Shell runtime."""

from __future__ import annotations

import asyncio
import copy
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any


TERMINAL_JOB_STATUSES = {"succeeded", "failed", "cancelled", "interrupted"}


class RuntimeBusy(RuntimeError):
    """Raised when an exclusive runtime operation cannot start."""

    def __init__(self, mode: str, active_job_id: str | None = None) -> None:
        self.mode = mode
        self.active_job_id = active_job_id
        detail = f"Runtime is busy ({mode})"
        if active_job_id:
            detail += f" with job {active_job_id}"
        super().__init__(detail)


class JobNotFound(KeyError):
    """Raised when a requested persisted job does not exist."""


class RuntimeJobManager:
    """Coordinate messages and exclusive background jobs for a BotRunner."""

    def __init__(self, runner: Any) -> None:
        self.runner = runner
        self.jobs_dir = Path(runner.session_dir) / "jobs"
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self._jobs: dict[str, dict[str, Any]] = {}
        self._active_job_id: str | None = None
        self._active_task: asyncio.Task[None] | None = None
        self._pending_messages = 0
        self._state_lock = asyncio.Lock()
        self._load_jobs()

    @property
    def mode(self) -> str:
        if self._active_job_id is not None:
            return "warping"
        if self._pending_messages:
            return "sending"
        return "idle"

    @property
    def active_job(self) -> dict[str, Any] | None:
        if self._active_job_id is None:
            return None
        return self.get_job(self._active_job_id)

    async def begin_message(self) -> None:
        async with self._state_lock:
            if self._active_job_id is not None:
                raise RuntimeBusy("warping", self._active_job_id)
            self._pending_messages += 1

    async def end_message(self) -> None:
        async with self._state_lock:
            self._pending_messages = max(0, self._pending_messages - 1)

    async def submit_warp(
        self,
        *,
        days: int,
        start: str | None,
        dry_run: bool,
    ) -> dict[str, Any]:
        async with self._state_lock:
            if self.runner.tick:
                raise RuntimeBusy("tick_enabled")
            if self._active_job_id is not None:
                raise RuntimeBusy("warping", self._active_job_id)
            if self._pending_messages:
                raise RuntimeBusy("sending")

            now = time.time()
            job_id = f"warp_{uuid.uuid4().hex[:12]}"
            job = {
                "id": job_id,
                "type": "warp",
                "status": "queued",
                "request": {
                    "days": days,
                    "start": start,
                    "dry_run": dry_run,
                },
                "progress": {"day": 0, "days": days},
                "result": None,
                "error": None,
                "created_at": now,
                "started_at": None,
                "finished_at": None,
                "updated_at": now,
            }
            self._persist(job)
            self._jobs[job_id] = job
            self._active_job_id = job_id
            try:
                self._active_task = asyncio.create_task(
                    self._run_warp(job_id),
                    name=f"dicepp-shell-{job_id}",
                )
            except Exception as exc:
                self._active_job_id = None
                self._active_task = None
                now = time.time()
                job["status"] = "failed"
                job["error"] = f"{type(exc).__name__}: {exc}"
                job["finished_at"] = now
                job["updated_at"] = now
                try:
                    self._persist(job)
                except OSError:
                    pass
                raise
            return self._snapshot(job)

    def get_job(self, job_id: str) -> dict[str, Any]:
        job = self._jobs.get(job_id)
        if job is None:
            raise JobNotFound(job_id)
        return self._snapshot(job)

    async def cancel_job(self, job_id: str) -> dict[str, Any]:
        async with self._state_lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise JobNotFound(job_id)
            if job["status"] in TERMINAL_JOB_STATUSES:
                return self._snapshot(job)
            if job_id != self._active_job_id or self._active_task is None:
                return self._snapshot(job)
            job["updated_at"] = time.time()
            job["status"] = "cancelling"
            job["cancel_requested"] = True
            task = self._active_task
            try:
                self._persist(job)
            except OSError:
                # Cancellation must still reach the task. A later terminal
                # write may succeed, and cleanup must never depend on I/O.
                pass

        task.cancel()
        task_error: Exception | None = None
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            task_error = exc

        # A task cancelled before its coroutine starts never enters
        # ``_run_warp`` and therefore cannot run that method's cleanup block.
        cleanup_error: OSError | None = None
        async with self._state_lock:
            if self._active_job_id == job_id:
                now = time.time()
                job["status"] = "cancelled"
                job["error"] = "Warp cancelled"
                job["finished_at"] = now
                job["updated_at"] = now
                try:
                    self._persist(job)
                except OSError as exc:
                    cleanup_error = exc
                finally:
                    self._active_job_id = None
                    self._active_task = None
            snapshot = self._snapshot(job)

        if cleanup_error is not None:
            raise cleanup_error
        if task_error is not None:
            raise task_error
        return snapshot

    async def shutdown(self) -> None:
        job_id = self._active_job_id
        if job_id is None:
            return
        await self.cancel_job(job_id)

    async def _run_warp(self, job_id: str) -> None:
        job = self._jobs[job_id]
        try:
            job["status"] = "running"
            job["started_at"] = time.time()
            job["updated_at"] = job["started_at"]
            self._persist(job)

            def update_progress(progress: dict[str, Any]) -> None:
                job["progress"] = dict(progress)
                job["updated_at"] = time.time()
                self._persist(job)

            request = job["request"]
            result = await self.runner.warp(
                days=request["days"],
                start=request["start"],
                dry_run=request["dry_run"],
                progress=update_progress,
            )
        except asyncio.CancelledError:
            job["status"] = "cancelled"
            job["error"] = "Warp cancelled"
        except Exception as exc:
            job["status"] = "failed"
            job["error"] = f"{type(exc).__name__}: {exc}"
        else:
            job["status"] = "succeeded"
            job["result"] = result
        finally:
            job["finished_at"] = time.time()
            job["updated_at"] = job["finished_at"]
            try:
                self._persist(job)
            finally:
                async with self._state_lock:
                    if self._active_job_id == job_id:
                        self._active_job_id = None
                        self._active_task = None

    def _load_jobs(self) -> None:
        now = time.time()
        for path in self.jobs_dir.glob("*.json"):
            try:
                job = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(job, dict) or not isinstance(job.get("id"), str):
                continue
            if job.get("status") in {"queued", "running", "cancelling"}:
                job["status"] = "interrupted"
                job["error"] = "Runtime exited before the job completed"
                job["finished_at"] = now
                job["updated_at"] = now
                self._persist(job)
            self._jobs[job["id"]] = job

    def _persist(self, job: dict[str, Any]) -> None:
        path = self.jobs_dir / f"{job['id']}.json"
        tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        tmp.write_text(
            json.dumps(job, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(tmp, path)

    @staticmethod
    def _snapshot(job: dict[str, Any]) -> dict[str, Any]:
        return copy.deepcopy(job)
