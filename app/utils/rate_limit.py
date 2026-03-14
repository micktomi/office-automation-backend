from __future__ import annotations

import time
from collections import defaultdict, deque


class InMemoryRateLimiter:
    """Simple in-memory sliding-window limiter for low-traffic deployments."""

    def __init__(self) -> None:
        self._windows: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str, limit: int, window_seconds: int) -> bool:
        now = time.time()
        bucket = self._windows[key]

        while bucket and bucket[0] <= now - window_seconds:
            bucket.popleft()

        if len(bucket) >= limit:
            return False

        bucket.append(now)
        return True
