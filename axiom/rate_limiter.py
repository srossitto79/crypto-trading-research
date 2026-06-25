"""Thread-safe rate limiter for API calls.

Uses a token bucket algorithm to throttle outbound API requests.
Default: 4 calls/minute (80% of Polygon.io free tier's 5/min).
"""

from __future__ import annotations

import asyncio
import threading
import time


class RateLimiter:
    """Token-bucket rate limiter.

    Args:
        calls_per_minute: Maximum calls allowed per minute.
    """

    def __init__(self, calls_per_minute: int = 4):
        self._interval = 60.0 / max(1, calls_per_minute)
        self._lock = threading.Lock()
        self._last_call = 0.0

    def acquire(self) -> None:
        """Block until a call is permitted (synchronous)."""
        with self._lock:
            now = time.monotonic()
            wait = self._last_call + self._interval - now
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.monotonic()

    async def async_acquire(self) -> None:
        """Block until a call is permitted (async)."""
        with self._lock:
            now = time.monotonic()
            wait = self._last_call + self._interval - now
            if wait > 0:
                # Release lock during sleep so other coroutines aren't blocked
                self._last_call = now + wait
        if wait > 0:
            await asyncio.sleep(wait)
        with self._lock:
            self._last_call = time.monotonic()

    @property
    def calls_per_minute(self) -> float:
        return 60.0 / self._interval
