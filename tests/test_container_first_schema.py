"""Container-first schema and ID semantics tests."""

from __future__ import annotations

import re
import json
from datetime import datetime, timezone

from axiom.db import (
    auto_assign_best_symbol,
    create_strategy_container,
    get_db,
    get_open_trades,
    init_db,
)


def test_backtest_results_table_has_strategy_fk(AXIOM_db):
    with get_db() as conn:
        fk_rows = conn.execute("PRAGMA foreign_key_list(backtest_results)").fetchall()

    assert any(
        str(row["table"]) == "strategies"
        and str(row["from"]) == "strategy_id"
        and str(row["to"]) == "id"
        for row in fk_rows
    )


def test_create_strategy_container_uses_canonical_s_id(AXIOM_db):
    with get_db() as conn:
        strategy_id, display_id, base_id = create_strategy_container(
            conn=conn,
            name="legacy-name",
            type_="macd",
            symbol="BTC",
            timeframe="1h",
            params={"fast": 12, "slow": 26, "signal": 9},
            strategy_id="legacy-custom-id",
        )
        row = conn.execute(
            "SELECT id, name, display_id, last_prefix, base_id FROM strategies WHERE id = ?",
            (strategy_id,),
        ).fetchone()

    assert re.fullmatch(r"S\d{5}", strategy_id)
    assert display_id == strategy_id
    assert int(base_id) == int(strategy_id[1:])
    assert row is not None
    assert str(row["name"]) == f"BTC-MACD-{strategy_id}"
    assert str(row["display_id"]) == strategy_id
    assert str(row["last_prefix"]) == "S"
    assert int(row["base_id"]) == int(strategy_id[1:])


def test_backtest_runs_backfills_backtest_results_on_migration(AXIOM_db):
    now_iso = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        strategy_id, _, _ = create_strategy_container(
            conn=conn,
            name="migration-source",
            type_="rsi_momentum",
            symbol="ETH",
            timeframe="15m",
            params={},
        )
        conn.execute(
            """
            INSERT INTO backtest_runs (run_id, strategy_id, is_metrics_json, oos_metrics_json, robustness_score, timestamp)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("B00001", strategy_id, '{"sharpe": 1.7}', '{"sharpe": 1.2}', 82.0, now_iso),
        )

    init_db()

    with get_db() as conn:
        row = conn.execute(
            "SELECT result_id, strategy_id, result_type, symbol, timeframe FROM backtest_results WHERE result_id = ?",
            ("B00001",),
        ).fetchone()

    assert row is not None
    assert str(row["result_id"]) == "B00001"
    assert str(row["strategy_id"]) == strategy_id
    assert str(row["result_type"]) == "backtest"
    # Bare base asset "ETH" is repaired to canonical pair "ETH/USDT" by
    # ``_normalize_strategy_symbol`` (see test_strategy_symbol_normalization).
    assert str(row["symbol"]) == "ETH/USDT"
    assert str(row["timeframe"]) == "15m"


def test_create_strategy_container_defaults_symbol_when_missing(AXIOM_db):
    with get_db() as conn:
        strategy_id, _, _ = create_strategy_container(
            conn=conn,
            name="missing-symbol",
            type_="ema_cross",
            symbol="",
            timeframe="1h",
            params={},
        )
        row = conn.execute(
            "SELECT name, symbol FROM strategies WHERE id = ?",
            (strategy_id,),
        ).fetchone()

    assert row is not None
    assert str(row["symbol"]) == "BTC/USDT"
    assert str(row["name"]) == f"BTC-EMA_CROSS-{strategy_id}"


def test_init_db_repairs_legacy_generic_strategy_identity(AXIOM_db):
    with get_db() as conn:
        strategy_id, _, _ = create_strategy_container(
            conn=conn,
            name="legacy-generic",
            type_="ema_cross",
            symbol="ETH/USDT",
            timeframe="1h",
            params={"asset": "SOL/USDT"},
        )
        conn.execute(
            "UPDATE strategies SET name = ?, symbol = ?, updated_at = ? WHERE id = ?",
            (f"GENERIC-EMA_CROSS-{strategy_id}", "", "1970-01-01T00:00:00+00:00", strategy_id),
        )

    init_db()

    with get_db() as conn:
        row = conn.execute(
            "SELECT name, symbol FROM strategies WHERE id = ?",
            (strategy_id,),
        ).fetchone()

    assert row is not None
    assert str(row["symbol"]) == "SOL/USDT"
    assert str(row["name"]) == f"SOL-EMA_CROSS-{strategy_id}"


def test_trades_schema_exposes_runtime_read_columns(AXIOM_db):
    required_columns = {
        "display_id",
        "strategy_name",
        "symbol",
        "pnl",
        "timeframe",
        "source",
        "created_at",
    }

    with get_db() as conn:
        columns = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(trades)").fetchall()
        }
        conn.execute(
            """
            INSERT INTO trades (
                id, display_id, strategy, strategy_name, strategy_id, asset, symbol,
                direction, entry_price, exit_price, size, leverage, pnl, pnl_pct,
                status, timeframe, source, opened_at, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "E99999",
                "E99999",
                "S00001",
                "Smoke Strategy",
                "S00001",
                "BTC",
                "BTC/USDT",
                "long",
                100.0,
                None,
                1.0,
                1.0,
                0.0,
                0.0,
                "OPEN",
                "1h",
                "paper",
                "2026-01-01T00:00:00+00:00",
                "2026-01-01T00:00:00+00:00",
            ),
        )

    assert required_columns.issubset(columns)

    rows = get_open_trades()
    matching = [row for row in rows if str(row["id"]) == "E99999"]
    assert matching
    assert "fill_entry_price" in matching[0]
    assert "signal_data" in matching[0]


def test_auto_assign_best_symbol_updates_timeframe_context(AXIOM_db):
    now_iso = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        strategy_id, _, _ = create_strategy_container(
            conn=conn,
            name="context-candidate",
            type_="ema_cross",
            symbol="BTC/USDT",
            timeframe="1h",
            params={},
        )
        low_metrics = {
            "sharpe": 0.8,
            "win_rate": 0.52,
            "profit_factor": 1.15,
            "max_drawdown_pct": 0.25,
            "total_trades": 28,
            "total_return_pct": 6.0,
        }
        high_metrics = {
            "sharpe": 1.9,
            "win_rate": 0.57,
            "profit_factor": 1.7,
            "max_drawdown_pct": 0.11,
            "total_trades": 45,
            "total_return_pct": 18.0,
        }
        conn.execute(
            """
            INSERT INTO backtest_results
            (result_id, strategy_id, result_type, symbol, timeframe, metrics_json, config_json, created_at)
            VALUES (?, ?, 'backtest', ?, ?, ?, '{}', ?)
            """,
            ("R-low-context", strategy_id, "BTC/USDT", "1h", json.dumps(low_metrics), now_iso),
        )
        conn.execute(
            """
            INSERT INTO backtest_results
            (result_id, strategy_id, result_type, symbol, timeframe, metrics_json, config_json, created_at)
            VALUES (?, ?, 'backtest', ?, ?, ?, '{}', ?)
            """,
            ("R-high-context", strategy_id, "ETH/USDT", "4h", json.dumps(high_metrics), now_iso),
        )

    assigned_symbol = auto_assign_best_symbol(strategy_id)
    assert assigned_symbol == "ETH/USDT"

    with get_db() as conn:
        row = conn.execute(
            "SELECT symbol, timeframe, name FROM strategies WHERE id = ?",
            (strategy_id,),
        ).fetchone()

    assert row is not None
    assert str(row["symbol"]) == "ETH/USDT"
    assert str(row["timeframe"]) == "4h"
    assert str(row["name"]) == f"ETH-EMA_CROSS-{strategy_id}"
