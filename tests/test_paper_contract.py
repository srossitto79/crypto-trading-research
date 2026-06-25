from __future__ import annotations

import json
from datetime import datetime, timezone

from axiom.api_domains import paper as paper_domain
from axiom.db import get_db, kv_set


def _insert_strategy(strategy_id: str, *, stage: str = "paper", params: dict | None = None) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO strategies
            (id, name, type, symbol, timeframe, params, metrics, status, owner, stage, stage_changed_at, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                strategy_id,
                "Paper Contract Strategy",
                "ema_cross",
                "BTC/USDT",
                "1h",
                json.dumps(params or {"fast": 12, "slow": 26}),
                json.dumps({"total_trades": 40, "sharpe": 1.2}),
                stage,
                "risk-manager",
                stage,
                now,
                now,
                now,
            ),
        )


def _insert_trade(strategy_id: str, *, status: str = "OPEN") -> None:
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO trades
            (id, strategy, strategy_id, asset, symbol, direction, entry_price, size, risk_pct,
             leverage, status, execution_type, signal_data, opened_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"{strategy_id}-trade-1",
                strategy_id,
                strategy_id,
                "BTC",
                "BTC/USDT",
                "long",
                100.0,
                2.0,
                0.01,
                1.5,
                status,
                "paper_challenger",
                json.dumps({"stop_loss": 95.0, "take_profit": 110.0}),
                now,
                now,
            ),
        )


def _insert_closed_trade(
    strategy_id: str,
    *,
    suffix: str = "1",
    entry_price: float = 100.0,
    exit_price: float = 103.0,
    size: float = 2.0,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO trades
            (id, strategy, strategy_id, asset, symbol, direction, entry_price, exit_price,
                fill_exit_price, size, leverage, status, execution_type, signal_data, opened_at, closed_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"{strategy_id}-trade-closed-{suffix}",
                strategy_id,
                strategy_id,
                "BTC",
                "BTC/USDT",
                "long",
                entry_price,
                None,
                exit_price,
                size,
                1.5,
                "CLOSED",
                "paper_challenger",
                json.dumps({"close_reason": "signal_exit"}),
                now,
                now,
                now,
            ),
        )


def test_paper_stage_strategy_projects_as_compat_session(AXIOM_db):
    _insert_strategy("paper-contract-1")

    sessions = paper_domain.get_paper_sessions()

    session = next(
        session
        for session in sessions
        if str(session["id"]).startswith("compat:strategy:paper-contract-1")
    )
    assert session["strategy_name"] == "Paper Contract Strategy"
    assert session["symbol"] == "BTC/USDT"
    assert session["timeframe"] == "1h"
    assert session["compat_kind"] == "paper"


def test_open_paper_trade_projects_position(AXIOM_db):
    _insert_strategy("paper-contract-2")
    _insert_trade("paper-contract-2")

    session = paper_domain.get_paper_session("compat:strategy:paper-contract-2")

    assert session["position"] is not None
    assert session["position"]["side"] == "long"
    assert session["position"]["entry_price"] == 100.0
    assert session["position"]["size"] == 2.0
    assert session["position"]["stop_loss_price"] == 95.0
    assert session["position"]["take_profit_price"] == 110.0


def test_closed_paper_trade_projection_uses_exit_fallbacks(AXIOM_db):
    _insert_strategy("paper-contract-3")
    _insert_closed_trade("paper-contract-3")

    trades = paper_domain.get_paper_session_trades("compat:strategy:paper-contract-3")

    assert len(trades) == 1
    trade = trades[0]
    assert trade["exit_price"] == 103.0
    # PAPER-1: dollar PnL excludes the leverage multiplier (matches the realized
    # close path): (103-100) * size 2.0 = 6.0, not 6.0 * leverage 1.5 = 9.0.
    assert trade["pnl"] == 6.0
    assert trade["close_reason"] == "signal_exit"


def test_paper_session_reports_closed_trade_performance_metrics(AXIOM_db):
    _insert_strategy("paper-contract-performance")
    _insert_closed_trade("paper-contract-performance", suffix="win", entry_price=100.0, exit_price=110.0, size=1.0)
    _insert_closed_trade("paper-contract-performance", suffix="loss", entry_price=100.0, exit_price=94.0, size=1.0)

    session = paper_domain.get_paper_session("compat:strategy:paper-contract-performance")

    assert session["performance"]["closed_trades"] == 2
    assert session["performance"]["win_rate_pct"] == 50.0
    # PAPER-1: dollar figures exclude leverage (win (110-100)*1.0=10.0, loss
    # (94-100)*1.0=-6.0); the PERCENT figures still carry leverage 1.5
    # (+15% / -9%), so ratios (profit_factor, avg_pnl_pct) are unchanged.
    assert session["performance"]["gross_profit"] == 10.0
    assert session["performance"]["gross_loss"] == -6.0
    assert session["performance"]["net_pnl"] == 4.0
    assert session["performance"]["avg_pnl"] == 2.0
    assert session["performance"]["avg_pnl_pct"] == 3.0
    assert session["performance"]["profit_factor"] == 1.6667
    assert session["performance"]["expectancy"] == 2.0
    assert session["performance"]["best_trade"] == 10.0
    assert session["performance"]["worst_trade"] == -6.0
    assert session["win_rate_pct"] == 50.0
    assert session["profit_factor"] == 1.6667


def test_flat_paper_session_reports_trade_mode_from_default_params(AXIOM_db):
    _insert_strategy(
        "paper-contract-short-only",
        params={"fast": 12, "slow": 26, "trade_mode": "short_only", "leverage": 2.0},
    )

    session = paper_domain.get_paper_session("compat:strategy:paper-contract-short-only")

    assert session["trade_mode"] == "short_only"
    assert session["position_model"] == "single_side"
    assert session["leverage"] == 2.0
    assert session["decision_params"]["trade_mode"] == "short_only"


def test_paper_session_prefers_scanner_canonical_decision_params(AXIOM_db):
    _insert_strategy(
        "paper-contract-canonical",
        params={"fast_ema": 9, "slow_ema": 21, "leverage": 1.0},
    )
    kv_set(
        "scanner_state",
        {
            "last_scan": "2026-03-11T23:19:00+00:00",
            "diagnostics": {
                "paper-contract-canonical": {
                    "runtime_source": "registry_ad_hoc",
                    "runtime_type": "ema_cross",
                    "canonical_params": {"ema_fast": 9, "ema_slow": 21, "leverage": 3.0},
                }
            },
        },
    )

    session = paper_domain.get_paper_session("compat:strategy:paper-contract-canonical")

    assert session["decision_params"] == {"ema_fast": 9, "ema_slow": 21, "leverage": 3.0}
    assert session["runtime_type"] == "ema_cross"
    assert session["runtime_source"] == "registry_ad_hoc"
    assert session["leverage"] == 3.0


def test_paper_session_surfaces_scanner_blocked_reason(AXIOM_db):
    _insert_strategy("paper-contract-regime-gated")
    kv_set(
        "scanner_state",
        {
            "last_scan": "2026-03-11T23:19:00+00:00",
            "diagnostics": {
                "paper-contract-regime-gated": {
                    "runtime_source": "runtime_type",
                    "runtime_type": "ema_cross",
                    "execution_decision": "blocked",
                    "blocked_reason": "regime gate: TREND_DOWN not allowed (confidence=0.40)",
                    "canonical_params": {"ema_fast": 12, "ema_slow": 26},
                }
            },
        },
    )

    session = paper_domain.get_paper_session("compat:strategy:paper-contract-regime-gated")

    assert session["blocked_reason"] == "regime gate: TREND_DOWN not allowed (confidence=0.40)"
    assert session["gated_by_regime"] is True
    assert session["gated_reason"] == "regime gate: TREND_DOWN not allowed (confidence=0.40)"
    assert session["status"] == "gated"


def test_paper_session_does_not_mark_static_regime_mismatch_as_gated(AXIOM_db):
    _insert_strategy("paper-contract-static-regime")
    with get_db() as conn:
        conn.execute(
            """
            UPDATE strategies
            SET compatible_regimes = ?, metrics = ?
            WHERE id = ?
            """,
            (
                json.dumps(["RANGE_BOUND"]),
                json.dumps({"compatible_regimes": ["RANGE_BOUND"], "total_trades": 40, "sharpe": 1.2}),
                "paper-contract-static-regime",
            ),
        )
    kv_set("regime:BTC", {"regime": "TREND_UP", "confidence": 0.9})
    kv_set(
        "scanner_state",
        {
            "last_scan": "2026-03-11T23:19:00+00:00",
            "diagnostics": {
                "paper-contract-static-regime": {
                    "runtime_source": "runtime_type",
                    "runtime_type": "ema_cross",
                    "execution_decision": "loaded",
                    "canonical_params": {"ema_fast": 12, "ema_slow": 26},
                }
            },
        },
    )

    session = paper_domain.get_paper_session("compat:strategy:paper-contract-static-regime")

    assert session["gated_by_regime"] is False
    assert session["gated_reason"] == ""
    assert session["status"] != "gated"


def test_paper_session_marks_non_regime_blocked_status(AXIOM_db):
    _insert_strategy("paper-contract-runtime-blocked")
    kv_set(
        "scanner_state",
        {
            "last_scan": "2026-03-11T23:19:00+00:00",
            "diagnostics": {
                "paper-contract-runtime-blocked": {
                    "runtime_source": "runtime_type",
                    "runtime_type": "unknown_runtime",
                    "execution_decision": "blocked",
                    "blocked_reason": "runtime type 'unknown_runtime' is not registered",
                    "canonical_params": {"ema_fast": 12, "ema_slow": 26},
                }
            },
        },
    )

    session = paper_domain.get_paper_session("compat:strategy:paper-contract-runtime-blocked")

    assert session["blocked_reason"] == "runtime type 'unknown_runtime' is not registered"
    assert session["gated_by_regime"] is False
    assert session["status"] == "blocked"
