from __future__ import annotations

import threading

from axiom.circuit_breaker import CircuitBreaker, State


def test_half_open_can_execute_is_capped_under_concurrency():
    breaker = CircuitBreaker(name="test", half_open_max_calls=1)
    breaker.state = State.HALF_OPEN

    barrier = threading.Barrier(3)
    results: list[bool] = []
    results_lock = threading.Lock()

    def _worker() -> None:
        barrier.wait()
        allowed = breaker.can_execute()
        with results_lock:
            results.append(allowed)

    threads = [threading.Thread(target=_worker) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert results.count(True) == 1
    assert results.count(False) == 2


def test_record_success_closes_half_open_once():
    breaker = CircuitBreaker(name="test")
    breaker.state = State.HALF_OPEN
    breaker.failure_count = 3
    breaker.half_open_calls = 1

    threads = [threading.Thread(target=breaker.record_success) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert breaker.state == State.CLOSED
    assert breaker.failure_count == 0
    assert breaker.half_open_calls == 0
