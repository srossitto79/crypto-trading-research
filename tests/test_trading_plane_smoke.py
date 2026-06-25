from __future__ import annotations

import json

from axiom.db import get_db
from axiom.trading_smoke import collect_trading_plane_smoke


def test_collect_trading_plane_smoke_passive_ok(AXIOM_db, monkeypatch):
    monkeypatch.setattr(
        "axiom.trading_smoke.get_account_value",
        lambda testnet=True, require_connection=True: {
            "accountValue": 1234.5,
            "totalMarginUsed": 12.0,
        },
    )
    monkeypatch.setattr(
        "axiom.trading_smoke.get_positions",
        lambda testnet=True: {"positions": [{"position": {"coin": "BTC", "szi": "0"}}]},
    )
    monkeypatch.setattr("axiom.trading_smoke.get_open_orders", lambda testnet=True: [])
    monkeypatch.setattr(
        "axiom.trading_smoke.get_all_mids",
        lambda testnet=True: {"SOL": 150.0, "ETH": 3400.0},
    )
    monkeypatch.setattr("axiom.trading_smoke.log_activity", lambda *args, **kwargs: None)

    report = collect_trading_plane_smoke()

    assert report["status"] == "ok"
    assert report["summary"]["mode"] == "passive"
    assert report["summary"]["account_value"] == 1234.5
    check_map = {check["name"]: check for check in report["checks"]}
    assert check_map["account"]["status"] == "ok"
    assert check_map["positions"]["details"]["position_count"] == 1
    assert check_map["execution"]["summary"] == "Active order smoke skipped"


def test_collect_trading_plane_smoke_active_order_path(AXIOM_db, monkeypatch):
    monkeypatch.setattr(
        "axiom.trading_smoke.get_account_value",
        lambda testnet=True, require_connection=True: {"accountValue": 5000.0, "totalMarginUsed": 0.0},
    )
    monkeypatch.setattr("axiom.trading_smoke.get_positions", lambda testnet=True: {"positions": []})
    monkeypatch.setattr("axiom.trading_smoke.get_open_orders", lambda testnet=True: [])
    monkeypatch.setattr("axiom.trading_smoke.get_all_mids", lambda testnet=True: {"SOL": 150.0})
    monkeypatch.setattr(
        "axiom.trading_smoke.market_order",
        lambda asset, side, size, stop_loss_price=None, testnet=True: {
            "entry_price": 151.0,
            "order_id": "open-1",
        },
    )
    monkeypatch.setattr(
        "axiom.trading_smoke.close_position",
        lambda asset, size, side="sell", testnet=True: {
            "close_price": 149.5,
            "order_id": "close-1",
        },
    )
    monkeypatch.setattr("axiom.trading_smoke.cancel_all_orders", lambda asset=None, testnet=True: [])
    monkeypatch.setattr("axiom.trading_smoke.log_activity", lambda *args, **kwargs: None)

    report = collect_trading_plane_smoke(
        place_test_order=True,
        asset="SOL",
        usd_notional=15.0,
    )

    assert report["status"] == "ok"
    execution = next(check for check in report["checks"] if check["name"] == "execution")
    trade_id = execution["details"]["trade_id"]
    assert execution["status"] == "ok"
    assert execution["details"]["asset"] == "SOL"

    with get_db() as conn:
        trade = conn.execute(
            "SELECT status, execution_type, signal_data FROM trades WHERE id = ?",
            (trade_id,),
        ).fetchone()
        portfolio_count = conn.execute(
            "SELECT COUNT(*) AS count FROM portfolio_positions WHERE trade_id = ?",
            (trade_id,),
        ).fetchone()

    assert trade is not None
    assert trade["status"] == "CLOSED"
    assert trade["execution_type"] == "soak_smoke"
    signal_data = json.loads(trade["signal_data"])
    assert signal_data["smoke_test"] is True
    assert int(portfolio_count["count"]) == 0


def test_collect_trading_plane_smoke_active_close_failure_retains_trade(AXIOM_db, monkeypatch):
    monkeypatch.setattr(
        "axiom.trading_smoke.get_account_value",
        lambda testnet=True, require_connection=True: {"accountValue": 5000.0, "totalMarginUsed": 0.0},
    )
    monkeypatch.setattr("axiom.trading_smoke.get_positions", lambda testnet=True: {"positions": []})
    monkeypatch.setattr("axiom.trading_smoke.get_open_orders", lambda testnet=True: [])
    monkeypatch.setattr("axiom.trading_smoke.get_all_mids", lambda testnet=True: {"SOL": 150.0})
    monkeypatch.setattr(
        "axiom.trading_smoke.market_order",
        lambda asset, side, size, stop_loss_price=None, testnet=True: {
            "entry_price": 151.0,
            "order_id": "open-1",
        },
    )
    monkeypatch.setattr(
        "axiom.trading_smoke.close_position",
        lambda asset, size, side="sell", testnet=True: {"error": "close failed"},
    )
    monkeypatch.setattr(
        "axiom.trading_smoke.cancel_all_orders",
        lambda asset=None, testnet=True: [{"coin": asset, "oid": "cleanup-1"}] if asset else [],
    )
    monkeypatch.setattr("axiom.trading_smoke.log_activity", lambda *args, **kwargs: None)

    report = collect_trading_plane_smoke(
        place_test_order=True,
        asset="SOL",
        usd_notional=15.0,
    )

    assert report["status"] == "fail"
    execution = next(check for check in report["checks"] if check["name"] == "execution")
    trade_id = execution["details"]["trade_id"]
    assert execution["status"] == "fail"
    assert execution["details"]["cleanup_required"] is True

    with get_db() as conn:
        trade = conn.execute(
            "SELECT status, signal_data FROM trades WHERE id = ?",
            (trade_id,),
        ).fetchone()
        portfolio_count = conn.execute(
            "SELECT COUNT(*) AS count FROM portfolio_positions WHERE trade_id = ?",
            (trade_id,),
        ).fetchone()

    assert trade is not None
    assert trade["status"] == "OPEN"
    signal_data = json.loads(trade["signal_data"])
    assert "smoke_close_error" in signal_data
    assert int(portfolio_count["count"]) == 1
