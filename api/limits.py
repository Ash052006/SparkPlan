"""Small in-memory per-client fixed-window rate limiter.

This protects the canonical ``POST /plan`` endpoint from accidental request
storms. It is process-local and deliberately not a distributed quota.
"""

from __future__ import annotations

import math
import os
import threading
import time
from collections.abc import Callable

DEFAULT_LIMIT = 10
DEFAULT_WINDOW_S = 60.0


def _positive_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _positive_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0 else default


class RateLimiter:
    """Thread-safe fixed-window limiter keyed by client IP."""

    def __init__(
        self,
        *,
        limit: int = DEFAULT_LIMIT,
        window_s: float = DEFAULT_WINDOW_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if limit < 1:
            raise ValueError("limit must be at least 1")
        if window_s <= 0:
            raise ValueError("window_s must be positive")
        self._limit = limit
        self._window_s = float(window_s)
        self._clock = clock
        self._lock = threading.Lock()
        self._hits: dict[str, tuple[float, int]] = {}

    @classmethod
    def from_env(cls) -> RateLimiter:
        return cls(
            limit=_positive_int("SPARKPLAN_RATE_LIMIT", DEFAULT_LIMIT),
            window_s=_positive_float("SPARKPLAN_RATE_WINDOW_S", DEFAULT_WINDOW_S),
        )

    def check(self, key: str) -> float | None:
        """Consume one request.

        Returns ``None`` when allowed, otherwise the number of seconds to wait
        before retrying.
        """
        now = self._clock()
        with self._lock:
            start, count = self._hits.get(key, (now, 0))
            if now - start >= self._window_s:
                start, count = now, 0
            if count >= self._limit:
                return max(0.0, self._window_s - (now - start))
            self._hits[key] = (start, count + 1)
            return None

    @property
    def limit(self) -> int:
        return self._limit

    @property
    def window_s(self) -> float:
        return self._window_s

    @staticmethod
    def retry_after_header(seconds: float) -> str:
        return str(max(1, math.ceil(seconds)))


__all__ = ["DEFAULT_LIMIT", "DEFAULT_WINDOW_S", "RateLimiter"]
