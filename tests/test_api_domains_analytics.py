from __future__ import annotations

import json
from datetime import datetime, timezone

from axiom.api_domains import analytics as analytics_domain
from axiom.db import get_db


def _insert_strategy(strategy_id: str, *, stage: str = "paper") -> None:
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
                strategy_id,
                "ema_cross",
                "BTC",
                "1h",
                "{}",
                json.dumps({"sharpe": 1.9, "total_trades": 90, "profit_factor": 1.6}),
                stage,
                "brain",
                stage,
                now,
                now,
                now,
            ),
        )


def test_get_pipeline_funnel_returns_counts_and_flows(AXIOM_db):
    _insert_strategy("S10001", stage="paper")
    _insert_strategy("S10002", stage="backtesting")
    now = datetime.now(timezone.utc).isoformat()

    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO strategy_events
            (strategy_id, from_state, to_state, actor, reason, owner_from, owner_to, details_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "S10001",
                "backtesting",
                "paper_trading",
                "brain",
                "Passed paper trading gate",
                "simulation-agent",
                "risk-manager",
                "{}",
                now,
            ),
        )

    payload = analytics_domain.get_pipeline_funnel()

    assert payload["counts"]["paper"] == 1
    assert payload["counts"]["backtesting"] == 1
    assert payload["flows"][0]["from_state"] == "backtesting"


def test_get_dashboard_overview_stub_shape(monkeypatch):
    monkeypatch.setattr(
        analytics_domain,
        "normalize_daemon_state",
        lambda write_back=True: {"running": True, "scan_count": 3, "last_scan": "2026-03-06T00:00:00+00:00"},
    )
    monkeypatch.setattr(analytics_domain, "is_trading_allowed", lambda: (True, "OK"))
    monkeypatch.setattr(
        analytics_domain,
        "get_strategies",
        lambda: [
            {"id": "S10001", "stage": "paper", "status": "paper", "metrics": json.dumps({"sharpe": 1.4})},
            {"id": "S10002", "stage": "backtesting", "status": "backtesting", "metrics": json.dumps({"sharpe": 2.1})},
        ],
    )

    payload = analytics_domain.get_dashboard_overview_stub()

    assert payload["kpis"]["total_tested"] == 2
    assert payload["kpis"]["active_scans"] == 3
    assert payload["autopilot"]["running"] is True
    assert payload["lifecycle_counts"]["paper"] == 1


def test_get_dashboard_leaderboard_stub_filters_by_symbol_and_tier(monkeypatch):
    analytics_domain.clear_dashboard_leaderboard_cache()
    monkeypatch.setattr(
        analytics_domain,
        "get_strategies",
        lambda: [
            {
                "id": "S10001",
                "name": "BTC Strong",
                "symbol": "BTC",
                "timeframe": "1h",
                "metrics": json.dumps({"sharpe_ratio": 1.8, "total_return": 12.5, "total_trades": 40}),
            },
            {
                "id": "S10002",
                "name": "ETH Weak",
                "symbol": "ETH",
                "timeframe": "1h",
                "metrics": json.dumps({"sharpe_ratio": -0.2, "total_return": -3.0, "total_trades": 12}),
            },
        ],
    )

    payload = analytics_domain.get_dashboard_leaderboard_stub(symbol="BTC", tier="strong")

    assert len(payload) == 1
    assert payload[0]["id"] == "S10001"
    assert payload[0]["tier"] == "strong"


def test_dashboard_leaderboard_cache_reuses_entries_within_ttl(monkeypatch):
    analytics_domain.clear_dashboard_leaderboard_cache()
    calls = {"count": 0}
    monotonic_values = iter([100.0, 100.0, 105.0, 105.0])

    def _fake_get_strategies():
        calls["count"] += 1
        return [
            {
                "id": f"S1000{calls['count']}",
                "name": "Cached Strategy",
                "symbol": "BTC",
                "timeframe": "1h",
                "metrics": json.dumps({"sharpe_ratio": 1.2}),
            }
        ]

    monkeypatch.setattr(analytics_domain, "get_strategies", _fake_get_strategies)
    monkeypatch.setattr(analytics_domain.time, "monotonic", lambda: next(monotonic_values))

    first = analytics_domain.get_dashboard_leaderboard_stub()
    second = analytics_domain.get_dashboard_tier_distribution_stub()

    assert calls["count"] == 1
    assert first[0]["id"] == "S10001"
    assert second["strong"] == 1


def test_dashboard_leaderboard_cache_refreshes_after_clear(monkeypatch):
    analytics_domain.clear_dashboard_leaderboard_cache()
    calls = {"count": 0}

    def _fake_get_strategies():
        calls["count"] += 1
        return [
            {
                "id": f"S2000{calls['count']}",
                "name": "Refresh Strategy",
                "symbol": "ETH",
                "timeframe": "4h",
                "metrics": json.dumps({"sharpe_ratio": 2.1}),
            }
        ]

    monkeypatch.setattr(analytics_domain, "get_strategies", _fake_get_strategies)
    monkeypatch.setattr(analytics_domain.time, "monotonic", lambda: 200.0)

    first = analytics_domain.get_dashboard_leaderboard_stub()
    analytics_domain.clear_dashboard_leaderboard_cache()
    second = analytics_domain.get_dashboard_leaderboard_stub()

    assert calls["count"] == 2
    assert first[0]["id"] == "S20001"
    assert second[0]["id"] == "S20002"
