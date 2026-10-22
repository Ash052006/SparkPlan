"""Bounded, thread-safe background execution for canonical runs.

Jobs are intentionally ephemeral. A completed run is still readable from its
persisted ``report.json`` after a process restart. Refusals and unsupported
ideas have no artifacts by design, so they are only available while the
process that received them is alive. No raw worker exception is exposed.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from concurrent.futures import Executor, ThreadPoolExecutor
from dataclasses import dataclass
from typing import Literal

from sparkplan.models.spec import Issue
from sparkplan.pipeline.run import STAGE_ORDER
from sparkplan.pipeline.run_models import RunResult, RunStageResult, RunStatus, StageStatus
from sparkplan.trace import new_run_id, utc_now

JobRunner = Callable[[str, str], RunResult]
JobState = Literal["queued", "running", "finished"]


class JobQueueFull(RuntimeError):
    """The bounded background queue cannot accept another job right now."""


@dataclass
class _Job:
    run_id: str
    idea: str
    created_at: str
    updated_at: str
    state: JobState = "queued"
    result: RunResult | None = None


def _worker_failure(run_id: str, created_at: str, updated_at: str) -> RunResult:
    issue = Issue(
        code="RUN_WORKER_FAILED",
        severity="error",
        message="The background run failed safely.",
        part_ids=[],
        fix_hint="Inspect the server log; no internal exception details are returned.",
    )
    stages = [
        RunStageResult(
            name="plan",
            status=StageStatus.FAILED,
            issues=[issue],
            reason=issue.message,
        )
    ]
    stages.extend(
        RunStageResult(
            name=name,
            status=StageStatus.SKIPPED,
            reason="not reached because the background worker failed",
        )
        for name in STAGE_ORDER[1:]
    )
    return RunResult(
        run_id=run_id,
        status=RunStatus.FAILED,
        stages=stages,
        issues=[issue],
        artifacts={},
        created_at=created_at,
        updated_at=updated_at,
        finished_at=updated_at,
    )


class RunJobManager:
    """Accept a bounded number of canonical runs and execute them in workers.

    ``max_workers`` bounds concurrent execution. ``max_pending`` bounds all
    queued plus running jobs so a burst of requests cannot grow an unbounded
    queue. The executor can be injected in tests for deterministic control.
    """

    def __init__(
        self,
        *,
        max_workers: int = 2,
        max_pending: int = 64,
        executor: Executor | None = None,
    ) -> None:
        if max_workers < 1:
            raise ValueError("max_workers must be at least 1")
        if max_pending < 1:
            raise ValueError("max_pending must be at least 1")
        self._lock = threading.Lock()
        self._jobs: dict[str, _Job] = {}
        self._pending = 0
        self._max_pending = max_pending
        self._executor: Executor = executor or ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="sparkplan-run",
        )

    def submit(self, idea: str, runner: JobRunner) -> str:
        """Register and enqueue one job, returning its generated run ID."""
        run_id = new_run_id()
        now = utc_now()
        with self._lock:
            if self._pending >= self._max_pending:
                raise JobQueueFull("background run queue is full")
            self._pending += 1
            self._jobs[run_id] = _Job(
                run_id=run_id,
                idea=idea,
                created_at=now,
                updated_at=now,
            )
        try:
            self._executor.submit(self._execute, run_id, idea, runner)
        except Exception:
            with self._lock:
                self._pending = max(0, self._pending - 1)
                self._jobs.pop(run_id, None)
            raise
        return run_id

    def _execute(self, run_id: str, idea: str, runner: JobRunner) -> None:
        with self._lock:
            job = self._jobs.get(run_id)
            if job is None:
                return
            job.state = "running"
            job.updated_at = utc_now()
        try:
            result = runner(idea, run_id)
            if not isinstance(result, RunResult):
                raise TypeError("job runner must return RunResult")
            if result.run_id != run_id:
                raise ValueError("job runner returned a different run_id")
        except Exception:
            now = utc_now()
            result = _worker_failure(run_id, job.created_at, now)
        with self._lock:
            current = self._jobs.get(run_id)
            if current is None:
                return
            current.result = result
            current.state = "finished"
            current.updated_at = result.finished_at or utc_now()
            self._pending = max(0, self._pending - 1)

    def get(self, run_id: str) -> RunResult | None:
        """Return a running placeholder or the final result, if known."""
        with self._lock:
            job = self._jobs.get(run_id)
            if job is None:
                return None
            if job.result is not None:
                return job.result
            return RunResult(
                run_id=job.run_id,
                status=RunStatus.RUNNING,
                stages=[],
                issues=[],
                artifacts={},
                created_at=job.created_at,
                updated_at=job.updated_at,
                finished_at=None,
            )

    def shutdown(self, *, wait: bool = False) -> None:
        if hasattr(self._executor, "shutdown"):
            self._executor.shutdown(wait=wait)

    @property
    def pending_count(self) -> int:
        with self._lock:
            return self._pending


__all__ = ["JobQueueFull", "JobRunner", "RunJobManager"]
