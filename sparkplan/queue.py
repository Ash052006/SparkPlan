"""Distributed asynchronous worker queue interface and implementations.

Provides an in-memory queue for tests/dev, and a Redis-backed queue
for horizontal scale-out production workers.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any, Protocol

logger = logging.getLogger("sparkplan.queue")


@dataclass
class QueuedTask:
    task_id: str
    task_name: str
    payload: dict[str, Any]
    status: str  # "queued", "running", "completed", "failed"
    created_at: float
    started_at: float | None = None
    finished_at: float | None = None
    result: dict[str, Any] | None = None
    error: str | None = None


class JobQueue(Protocol):
    """Protocol for background job execution."""

    def submit(self, task_name: str, payload: dict[str, Any]) -> str:
        """Enqueue task, return task_id."""
        ...

    def get_task(self, task_id: str) -> QueuedTask | None:
        """Retrieve task state."""
        ...

    def cancel(self, task_id: str) -> bool:
        """Cancel queued task if not yet completed."""
        ...


class InMemoryJobQueue:
    """Threadpool-based in-memory job queue for tests and single-container deployments."""

    def __init__(self, max_workers: int = 4) -> None:
        self._tasks: dict[str, QueuedTask] = {}
        self._lock = threading.Lock()
        self._queue: queue.Queue[QueuedTask | None] = queue.Queue()
        self._handlers: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {}
        self._workers: list[threading.Thread] = []
        self._running = True

        for i in range(max_workers):
            t = threading.Thread(target=self._worker_loop, daemon=True, name=f"mem-worker-{i}")
            t.start()
            self._workers.append(t)

    def register_handler(
        self, task_name: str, handler: Callable[[dict[str, Any]], dict[str, Any]]
    ) -> None:
        self._handlers[task_name] = handler

    def submit(self, task_name: str, payload: dict[str, Any]) -> str:
        task_id = f"task_{uuid.uuid4().hex[:12]}"
        task = QueuedTask(
            task_id=task_id,
            task_name=task_name,
            payload=payload,
            status="queued",
            created_at=time.time(),
        )
        with self._lock:
            self._tasks[task_id] = task
        self._queue.put(task)
        return task_id

    def get_task(self, task_id: str) -> QueuedTask | None:
        with self._lock:
            return self._tasks.get(task_id)

    def cancel(self, task_id: str) -> bool:
        with self._lock:
            task = self._tasks.get(task_id)
            if task and task.status == "queued":
                task.status = "failed"
                task.error = "Task cancelled by client"
                task.finished_at = time.time()
                return True
        return False

    def _worker_loop(self) -> None:
        while self._running:
            try:
                task = self._queue.get(timeout=0.5)
                if task is None:
                    break
                with self._lock:
                    if task.status != "queued":
                        self._queue.task_done()
                        continue
                    task.status = "running"
                    task.started_at = time.time()

                handler = self._handlers.get(task.task_name)
                if not handler:
                    with self._lock:
                        task.status = "failed"
                        task.error = f"No handler registered for {task.task_name}"
                        task.finished_at = time.time()
                else:
                    try:
                        res = handler(task.payload)
                        with self._lock:
                            task.status = "completed"
                            task.result = res
                            task.finished_at = time.time()
                    except Exception as err:
                        logger.exception("Worker error running %s", task.task_name)
                        with self._lock:
                            task.status = "failed"
                            task.error = str(err)
                            task.finished_at = time.time()
                self._queue.task_done()
            except queue.Empty:
                continue
            except Exception:
                continue

    def stop(self) -> None:
        self._running = False
        for _ in self._workers:
            self._queue.put(None)


class RedisJobQueue:
    """Redis-backed distributed worker queue for multi-container deployments."""

    def __init__(
        self,
        redis_url: str = "redis://localhost:6379/0",
        queue_name: str = "sparkplan_jobs",
    ) -> None:
        self.redis_url = redis_url
        self.queue_name = queue_name
        self._client: Any = None
        self._init_client()

    def _init_client(self) -> None:
        try:
            import redis  # type: ignore[import-untyped]

            self._client = redis.Redis.from_url(self.redis_url, decode_responses=True)
        except Exception:
            self._client = None

    def submit(self, task_name: str, payload: dict[str, Any]) -> str:
        task_id = f"task_{uuid.uuid4().hex[:12]}"
        task = QueuedTask(
            task_id=task_id,
            task_name=task_name,
            payload=payload,
            status="queued",
            created_at=time.time(),
        )
        if self._client:
            try:
                data = json.dumps(asdict(task))
                self._client.set(f"sparkplan:task:{task_id}", data, ex=86400)
                self._client.rpush(self.queue_name, task_id)
                return task_id
            except Exception as err:
                logger.warning("Redis unavailable: %s", err)
        return task_id

    def get_task(self, task_id: str) -> QueuedTask | None:
        if not self._client:
            return None
        try:
            raw = self._client.get(f"sparkplan:task:{task_id}")
            if not raw:
                return None
            data = json.loads(raw)
            return QueuedTask(**data)
        except Exception:
            return None

    def cancel(self, task_id: str) -> bool:
        if not self._client:
            return False
        try:
            raw = self._client.get(f"sparkplan:task:{task_id}")
            if not raw:
                return False
            data = json.loads(raw)
            if data.get("status") == "queued":
                data["status"] = "failed"
                data["error"] = "Task cancelled by client"
                data["finished_at"] = time.time()
                self._client.set(f"sparkplan:task:{task_id}", json.dumps(data), ex=86400)
                return True
            return False
        except Exception:
            return False


def get_default_queue() -> JobQueue:
    redis_url = os.environ.get("REDIS_URL")
    if redis_url:
        return RedisJobQueue(redis_url=redis_url)
    return InMemoryJobQueue()


__all__ = [
    "InMemoryJobQueue",
    "JobQueue",
    "QueuedTask",
    "RedisJobQueue",
    "get_default_queue",
]
