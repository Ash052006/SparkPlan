from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest
from api.jobs import JobQueueFull, RunJobManager
from api.limits import RateLimiter
from sparkplan.pipeline.run_models import RunResult


class DeferredExecutor:
    def __init__(self) -> None:
        self.tasks: list[Any] = []

    def submit(self, fn: Any, *args: Any, **kwargs: Any) -> object:
        self.tasks.append((fn, args, kwargs))
        return object()

    def shutdown(self, *, wait: bool = False) -> None:
        return None


def test_bounded_job_queue_rejects_extra_jobs() -> None:
    executor = DeferredExecutor()
    manager = RunJobManager(executor=executor, max_pending=1)

    manager.submit("one", lambda idea, run_id: None)  # type: ignore[arg-type,return-value]

    with pytest.raises(JobQueueFull):
        manager.submit("two", lambda idea, run_id: None)  # type: ignore[arg-type,return-value]


def test_rate_limiter_is_thread_safe() -> None:
    limiter = RateLimiter(limit=5, window_s=60, clock=lambda: 0.0)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: limiter.check("client"), range(40)))

    assert sum(result is None for result in results) == 5
    assert all(result == 60.0 for result in results if result is not None)


def test_job_manager_fails_safely_when_runner_returns_wrong_type() -> None:
    executor = DeferredExecutor()
    manager = RunJobManager(executor=executor)

    run_id = manager.submit("idea", lambda idea, run_id: "not a result")  # type: ignore[arg-type,return-value]
    fn, args, kwargs = executor.tasks.pop(0)
    fn(*args, **kwargs)

    result = manager.get(run_id)
    assert isinstance(result, RunResult)
    assert result.status.value == "failed"
    assert result.issues[0].code == "RUN_WORKER_FAILED"
