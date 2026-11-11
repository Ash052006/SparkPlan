"""Unit tests for the distributed worker queue abstraction (sparkplan/queue.py)."""

from __future__ import annotations

import time

from sparkplan.queue import InMemoryJobQueue, QueuedTask, get_default_queue


def test_in_memory_queue_executes_task() -> None:
    q = InMemoryJobQueue(max_workers=2)

    def echo_handler(payload: dict[str, str]) -> dict[str, str]:
        return {"echo": payload.get("msg", "")}

    q.register_handler("echo", echo_handler)

    task_id = q.submit("echo", {"msg": "hello esp32"})
    assert task_id.startswith("task_")

    # Wait briefly for worker thread
    deadline = time.time() + 2.0
    completed_task: QueuedTask | None = None
    while time.time() < deadline:
        task = q.get_task(task_id)
        if task and task.status == "completed":
            completed_task = task
            break
        time.sleep(0.05)

    assert completed_task is not None
    assert completed_task.status == "completed"
    assert completed_task.result == {"echo": "hello esp32"}
    assert completed_task.finished_at is not None

    q.stop()


def test_in_memory_queue_handles_failure() -> None:
    q = InMemoryJobQueue(max_workers=1)

    def failing_handler(payload: dict[str, str]) -> dict[str, str]:
        raise ValueError("Simulated silicon compilation failure")

    q.register_handler("fail", failing_handler)

    task_id = q.submit("fail", {})
    deadline = time.time() + 2.0
    failed_task: QueuedTask | None = None
    while time.time() < deadline:
        task = q.get_task(task_id)
        if task and task.status == "failed":
            failed_task = task
            break
        time.sleep(0.05)

    assert failed_task is not None
    assert failed_task.status == "failed"
    assert "Simulated silicon compilation failure" in (failed_task.error or "")

    q.stop()


def test_in_memory_queue_cancel() -> None:
    q = InMemoryJobQueue(max_workers=0)  # No workers, task stays queued
    task_id = q.submit("dummy", {})

    task = q.get_task(task_id)
    assert task is not None
    assert task.status == "queued"

    cancelled = q.cancel(task_id)
    assert cancelled is True

    updated = q.get_task(task_id)
    assert updated is not None
    assert updated.status == "failed"
    assert "cancelled" in (updated.error or "").lower()

    q.stop()


def test_default_queue_fallback() -> None:
    q = get_default_queue()
    assert isinstance(q, InMemoryJobQueue)
