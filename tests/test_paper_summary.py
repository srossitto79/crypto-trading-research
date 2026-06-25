"""Tests for the paper session PnL rollup (`/api/paper/summary`).

Focus: close_reason breakdown correctness — reconciler/stale closes must stay
visible and distinct from strategy exits — plus realized PnL / win-rate math.
"""

from axiom.api_domains import paper as paper_domain


def _trade(pnl=None, close_reason=None, **extra):
    row = {
        "id": "T1",
        "symbol": "BTC/USDT",
        "side": "long",
        "pnl": pnl,
        "close_reason": close_reason,
    }
    row.update(extra)
    return row


def _session(trades, positions=None, **extra):
    row = {
        "id": "compat:strategy:S1",
        "strategy_id": "S1",
        "strategy_name": "Alpha",
        "symbol": "BTC/USDT",
        "timeframe": "1h",
        "status": "watching",
        "trades": trades,
        "positions": positions or [],
    }
    row.update(extra)
    return row


def test_summary_close_reason_breakdown_counts_each_reason():
    sessions = [
        _session(
            [
                _trade(pnl=10.0, close_reason="exit_signal"),
                _trade(pnl=-5.0, close_reason="exit_signal"),
                _trade(pnl=-1.0, close_reason="reconcile_missing_on_exchange"),
                _trade(pnl=2.0, close_reason="stale_position_sweep"),
                _trade(pnl=3.0, close_reason=None),
            ]
        )
    ]

    summary = paper_domain._summarize_paper_sessions(sessions)
    row = summary["sessions"][0]

    assert row["close_reasons"] == {
        "exit_signal": 2,
        "reconcile_missing_on_exchange": 1,
        "stale_position_sweep": 1,
        "unspecified": 1,
    }
    assert summary["totals"]["close_reasons"] == row["close_reasons"]


def test_summary_normalizes_close_reason_case_and_whitespace():
    sessions = [
        _session(
            [
                _trade(pnl=1.0, close_reason="Exit_Signal"),
                _trade(pnl=1.0, close_reason="  exit_signal  "),
                _trade(pnl=1.0, close_reason=""),
                _trade(pnl=1.0, close_reason="   "),
            ]
        )
    ]

    summary = paper_domain._summarize_paper_sessions(sessions)
    assert summary["sessions"][0]["close_reasons"] == {
        "exit_signal": 2,
        "unspecified": 2,
    }


def test_summary_realized_pnl_win_rate_and_open_count():
    sessions = [
        _session(
            [
                _trade(pnl=10.0, close_reason="exit_signal"),
                _trade(pnl=-4.0, close_reason="exit_signal"),
                # Incomplete close: counted in close_reasons / closed_count but
                # contributes nothing to realized PnL or wins.
                _trade(pnl=None, close_reason="reconcile_missing_on_exchange"),
            ],
            positions=[{"id": "P1", "side": "long"}, {"id": "P2", "side": "short"}],
        )
    ]

    summary = paper_domain._summarize_paper_sessions(sessions)
    row = summary["sessions"][0]

    assert row["closed_count"] == 3
    assert row["open_count"] == 2
    assert row["realized_pnl_usd"] == 6.0
    # 1 win out of 3 closed trades (rounded to 4 decimals server-side).
    assert abs(row["win_rate_pct"] - (1 / 3) * 100.0) < 1e-3

    totals = summary["totals"]
    assert totals["session_count"] == 1
    assert totals["closed_count"] == 3
    assert totals["open_count"] == 2
    assert totals["realized_pnl_usd"] == 6.0


def test_summary_aggregates_across_sessions():
    sessions = [
        _session(
            [_trade(pnl=5.0, close_reason="exit_signal")],
            id="compat:strategy:S1",
            strategy_id="S1",
        ),
        _session(
            [
                _trade(pnl=-2.0, close_reason="reconcile_missing_on_exchange"),
                _trade(pnl=4.0, close_reason="exit_signal"),
            ],
            id="compat:strategy:S2",
            strategy_id="S2",
            strategy_name="Beta",
        ),
    ]

    summary = paper_domain._summarize_paper_sessions(sessions)
    totals = summary["totals"]

    assert totals["session_count"] == 2
    assert totals["closed_count"] == 3
    assert totals["realized_pnl_usd"] == 7.0
    assert totals["close_reasons"] == {
        "exit_signal": 2,
        "reconcile_missing_on_exchange": 1,
    }
    # Per-session rows keep their own breakdowns.
    by_strategy = {row["strategy_id"]: row for row in summary["sessions"]}
    assert by_strategy["S1"]["close_reasons"] == {"exit_signal": 1}
    assert by_strategy["S2"]["close_reasons"] == {
        "exit_signal": 1,
        "reconcile_missing_on_exchange": 1,
    }


def test_summary_empty_sessions_yield_zeroed_totals():
    summary = paper_domain._summarize_paper_sessions([])
    totals = summary["totals"]
    assert summary["sessions"] == []
    assert totals["session_count"] == 0
    assert totals["closed_count"] == 0
    assert totals["open_count"] == 0
    assert totals["realized_pnl_usd"] == 0.0
    assert totals["win_rate_pct"] is None
    assert totals["close_reasons"] == {}


def test_summary_session_with_no_trades_has_none_win_rate():
    summary = paper_domain._summarize_paper_sessions([_session([], positions=[{"id": "P1"}])])
    row = summary["sessions"][0]
    assert row["closed_count"] == 0
    assert row["open_count"] == 1
    assert row["win_rate_pct"] is None
    assert row["realized_pnl_usd"] == 0.0
    assert row["close_reasons"] == {}


def test_get_paper_summary_uses_uncapped_trades_and_stamps_metadata(monkeypatch):
    captured: dict = {}

    def fake_collect(include_deployed=False, session_limit=None, trades_limit=500):
        captured["include_deployed"] = include_deployed
        captured["trades_limit"] = trades_limit
        return [_session([_trade(pnl=1.0, close_reason="exit_signal")])]

    monkeypatch.setattr(paper_domain, "_collect_compat_paper_sessions", fake_collect)

    summary = paper_domain.get_paper_summary(include_deployed=True)

    assert captured["include_deployed"] is True
    assert captured["trades_limit"] == paper_domain._SUMMARY_TRADES_LIMIT
    assert summary["include_deployed"] is True
    assert summary["timestamp"]
    assert summary["totals"]["closed_count"] == 1
