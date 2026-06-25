"""Tests for rate limiter utility."""

from __future__ import annotations

import time
import threading

from axiom.rate_limiter import RateLimiter


def test_rate_limiter_basic():
    rl = RateLimiter(calls_per_minute=60)  # 1 per second
    start = time.monotonic()
    rl.acquire()
    rl.acquire()
    elapsed = time.monotonic() - start
    # Second call should wait ~1 second
    assert elapsed >= 0.9


def test_rate_limiter_fast():
    rl = RateLimiter(calls_per_minute=6000)  # Very fast, negligible delay
    start = time.monotonic()
    for _ in range(5):
        rl.acquire()
    elapsed = time.monotonic() - start
    assert elapsed < 1.0


def test_rate_limiter_property():
    rl = RateLimiter(calls_per_minute=10)
    assert rl.calls_per_minute == 10.0


def test_rate_limiter_thread_safety():
    rl = RateLimiter(calls_per_minute=600)  # 10 per second
    call_times: list[float] = []
    lock = threading.Lock()

    def worker():
        rl.acquire()
        with lock:
            call_times.append(time.monotonic())

    threads = [threading.Thread(target=worker) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert len(call_times) == 5
