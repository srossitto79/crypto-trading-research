"""Regression guards for the 2026-06-18 overnight halt.

Hyperliquid testnet 504 bursts tripped the shared hl_account circuit breaker;
the daemon then escalated a pure READ/connectivity failure into an
operator-required hard halt that froze unattended trading for hours. These tests
lock in the fix: a connectivity failure is a SOFT, self-healing
'exchange_unreachable' state (recovery_active stays False, no operator required),
escalating to a hard halt only after a SUSTAINED outage measured by elapsed time
— while a real DB-vs-exchange divergence still hard-halts immediately. Plus the
breaker no longer trips on bursty gateway timeouts, and get_open_trades returns
the `strategy` label the UI renders.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from axiom import daemon
from axiom.circuit_breaker import CircuitBreaker, State
from axiom.exchange import hyperliquid
from axiom.exchange.risk import reconcile_exchange_positions


# ---------------------------------------------------------------------------
# FIX 1: reconcile read-failure -> soft, self-healing state (no hard halt)
# ---------------------------------------------------------------------------

_FETCH_ERR = {
    "error": "Could not fetch exchange positions: circuit breaker 'account' is open",
    "error_kind": "fetch_unavailable",
}


def test_connectivity_error_is_soft_not_operator_halt():
    state = {"recovery_network": "testnet"}
    daemon._update_recovery_state_from_reconcile(state, dict(_FETCH_ERR), source="periodic")

    # The trading gate keys on recovery_active — it must stay False so entries are
    # NOT frozen by a transient testnet blip (they remain fail-closed per-call by
    # can_open Rule 0c instead).
    assert state["recovery_active"] is False
    assert state["recovery_requires_operator"] is False
    assert state["recovery_status"] == "exchange_unreachable"
    assert state.get("recovery_first_unreachable_at")


def test_connectivity_burst_does_not_escalate_on_count():
    """A 504 burst (many consecutive errors) must NOT hard-halt within the
    outage window — the bug was count-based escalation at 3."""
    state = {"recovery_network": "testnet"}
    for _ in range(8):
        daemon._update_recovery_state_from_reconcile(state, dict(_FETCH_ERR), source="periodic")
    assert state["recovery_active"] is False
    assert state["recovery_requires_operator"] is False
    assert state["recovery_status"] == "exchange_unreachable"


def test_sustained_outage_eventually_hard_halts():
    """After a SUSTAINED outage (elapsed time past the threshold) the soft state
    escalates to a real operator-required halt — the sole remaining outage
    detector once the breaker is 504-tolerant."""
    old = (datetime.now(timezone.utc) - timedelta(seconds=daemon._RECONCILE_OUTAGE_ESCALATE_SECONDS + 60)).isoformat()
    state = {"recovery_network": "testnet", "recovery_first_unreachable_at": old}
    daemon._update_recovery_state_from_reconcile(state, dict(_FETCH_ERR), source="periodic")
    assert state["recovery_active"] is True
    assert state["recovery_requires_operator"] is True
    assert state["recovery_status"] == "error"
    assert "sustained outage" in state["recovery_summary"]


def test_connectivity_error_preserves_prior_genuine_block():
    """A read failure must NOT unblock a previously-latched divergence halt."""
    state = {
        "recovery_network": "testnet",
        "recovery_active": True,
        "recovery_requires_operator": True,
        "recovery_status": "blocked",
    }
    daemon._update_recovery_state_from_reconcile(state, dict(_FETCH_ERR), source="periodic")
    assert state["recovery_active"] is True  # carried forward
    assert state["recovery_requires_operator"] is True


def test_real_divergence_still_hard_halts_immediately():
    state = {"recovery_network": "testnet"}
    daemon._update_recovery_state_from_reconcile(
        state,
        {
            "synced": False,
            "exchange_open": 1,
            "discrepancies": [{"type": "missing_in_sqlite", "details": "BTC orphan"}],
        },
        source="periodic",
    )
    assert state["recovery_active"] is True
    assert state["recovery_requires_operator"] is True
    assert state["recovery_status"] == "blocked"


def test_clean_reconcile_clears_outage_timer_and_resolves():
    state = {
        "recovery_network": "testnet",
        "recovery_active": True,
        "recovery_first_unreachable_at": datetime.now(timezone.utc).isoformat(),
    }
    daemon._update_recovery_state_from_reconcile(
        state, {"synced": True, "exchange_open": 0, "discrepancies": []}, source="periodic"
    )
    assert state["recovery_active"] is False
    assert state["recovery_status"] == "resolved"
    assert state["recovery_first_unreachable_at"] is None


def test_non_connectivity_error_keeps_count_escalation():
    """A genuinely unexpected (non-connectivity) reconcile error keeps the
    original CR-2 count-based escalation so it isn't silently ignored."""
    state = {"recovery_network": "testnet"}
    weird = {"error": "ValueError: unexpected payload shape"}
    for _ in range(daemon._RECONCILE_ERROR_ESCALATE_AFTER - 1):
        daemon._update_recovery_state_from_reconcile(state, dict(weird), source="periodic")
        assert state["recovery_active"] is False
    daemon._update_recovery_state_from_reconcile(state, dict(weird), source="periodic")
    assert state["recovery_active"] is True
    assert state["recovery_requires_operator"] is True


def test_startup_preflight_preserves_genuine_block_on_boot_504(monkeypatch):
    """Finding A: a carried-forward genuine divergence block must NOT be cleared
    if the first boot reconcile then hits a transient 504."""
    state: dict[str, object] = {
        "recovery_active": True,
        "recovery_status": "blocked",
        "recovery_discrepancy_count": 1,
    }
    monkeypatch.setattr(daemon, "get_execution_mode", lambda: "live")
    monkeypatch.setattr(daemon, "_get_testnet", lambda: True)
    monkeypatch.setattr(daemon, "_exchange_credentials_status", lambda: (True, None))
    monkeypatch.setattr(daemon, "sync_from_trades", lambda: 0)

    def _boom(*_a, **_k):
        raise RuntimeError("HTTP Error 504: Gateway Timeout")

    monkeypatch.setattr(daemon, "get_positions", _boom)
    monkeypatch.setattr(daemon, "get_open_orders", _boom)
    monkeypatch.setattr(daemon, "get_account_value", _boom)

    recovery = daemon.run_startup_recovery_preflight(state)
    # Genuine block preserved (NOT downgraded to a soft exchange_unreachable state).
    assert recovery["recovery_active"] is True


def test_startup_preflight_connectivity_error_is_soft(monkeypatch):
    """A boot-time 504 must not latch an operator halt across the restart."""
    state: dict[str, object] = {}
    monkeypatch.setattr(daemon, "get_execution_mode", lambda: "live")
    monkeypatch.setattr(daemon, "_get_testnet", lambda: True)
    monkeypatch.setattr(daemon, "_exchange_credentials_status", lambda: (True, None))

    def _boom(*_a, **_k):
        raise RuntimeError("HTTP Error 504: Gateway Timeout")

    monkeypatch.setattr(daemon, "get_positions", _boom)
    monkeypatch.setattr(daemon, "get_open_orders", _boom)
    monkeypatch.setattr(daemon, "get_account_value", _boom)
    monkeypatch.setattr(daemon, "sync_from_trades", lambda: 0)

    recovery = daemon.run_startup_recovery_preflight(state)
    assert recovery["recovery_active"] is False
    assert recovery["recovery_requires_operator"] is False
    assert recovery["recovery_status"] == "exchange_unreachable"


# ---------------------------------------------------------------------------
# FIX 1: reconcile tags fetch failures so the daemon classifies on a tag
# ---------------------------------------------------------------------------

def test_reconcile_tags_fetch_failures(monkeypatch):
    monkeypatch.setattr(
        "axiom.exchange.risk._snapshot_exchange_state",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("circuit breaker 'account' is open")),
    )
    recon = reconcile_exchange_positions(testnet=True)
    assert recon.get("error_kind") == "fetch_unavailable"
    assert daemon._is_reconcile_fetch_unavailable(recon) is True


def test_fetch_unavailable_never_classifies_a_divergence():
    # A result carrying real discrepancies must never be treated as a fetch blip.
    assert daemon._is_reconcile_fetch_unavailable(
        {"error_kind": "fetch_unavailable", "discrepancies": [{"type": "x"}]}
    ) is False


# ---------------------------------------------------------------------------
# FIX 1b: breaker tolerates bursty gateway timeouts on the READ path
# ---------------------------------------------------------------------------

def test_is_transient_upstream_matches_gateway_timeouts():
    assert hyperliquid._is_transient_upstream(RuntimeError("HTTP Error 504: Gateway Timeout"))
    assert hyperliquid._is_transient_upstream(RuntimeError("502 Bad Gateway"))
    assert hyperliquid._is_transient_upstream(TimeoutError("read timed out"))
    # A 429 is handled separately and is NOT an upstream outage classification here.
    assert not hyperliquid._is_transient_upstream(RuntimeError("some logic error"))


def test_with_breaker_does_not_trip_on_504_burst(monkeypatch):
    monkeypatch.setattr(hyperliquid.time, "sleep", lambda *_a, **_k: None)
    breaker = CircuitBreaker(name="hl_account", failure_threshold=4)
    calls = {"n": 0}

    def _always_504():
        calls["n"] += 1
        raise RuntimeError("HTTP Error 504: Gateway Timeout")

    try:
        hyperliquid._with_breaker("account", breaker, _always_504)
    except RuntimeError as exc:
        assert "504" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected the transient error to re-raise after retries")

    # Retried (more than one attempt) and, crucially, the breaker stayed CLOSED.
    assert calls["n"] == hyperliquid._HL_RATELIMIT_MAX_ATTEMPTS
    assert breaker.state is State.CLOSED
    assert breaker.failure_count == 0


def test_with_breaker_still_trips_on_real_error(monkeypatch):
    breaker = CircuitBreaker(name="hl_account", failure_threshold=2)
    def _real_error():
        raise RuntimeError("malformed response")
    for _ in range(2):
        try:
            hyperliquid._with_breaker("account", breaker, _real_error)
        except RuntimeError:
            pass
    assert breaker.state is State.OPEN


# ---------------------------------------------------------------------------
# FIX 3: get_open_trades returns the `strategy` label the open-positions UI renders
# ---------------------------------------------------------------------------

def test_get_open_trades_includes_strategy_column(AXIOM_db):
    from axiom.db import get_db, get_open_trades

    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO trades
            (id, strategy, strategy_name, strategy_id, asset, symbol, direction,
             entry_price, size, risk_pct, leverage, status, execution_type, source, opened_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', 'live', 'live', ?)
            """,
            ("E9001", "S01601", "S01601", "S01601", "APT", "APT", "short",
             0.6674, 159.04, 0.01, 1.0, "2026-06-18T03:04:02+00:00"),
        )
    rows = get_open_trades()
    assert rows, "expected the inserted OPEN trade"
    assert "strategy" in rows[0]
    assert rows[0]["strategy"] == "S01601"


def test_price_breaker_not_retried(monkeypatch):
    """The price breaker must keep tripping (no transient retry) so get_all_mids'
    cached-mid fast path stays fast for the emergency close."""
    monkeypatch.setattr(hyperliquid.time, "sleep", lambda *_a, **_k: None)
    breaker = CircuitBreaker(name="hl_price", failure_threshold=1)
    calls = {"n": 0}

    def _504():
        calls["n"] += 1
        raise RuntimeError("HTTP Error 504: Gateway Timeout")

    try:
        hyperliquid._with_breaker("prices", breaker, _504)
    except RuntimeError:
        pass
    assert calls["n"] == 1  # no retry on the price path
    assert breaker.state is State.OPEN  # tripped, so the cached fast path engages
