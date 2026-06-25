from __future__ import annotations

import json
from datetime import datetime, timezone


def test_active_coins_includes_active_strategy_symbols(AXIOM_db):
    from axiom.daemon import _active_coins
    from axiom.db import get_db

    now = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO strategies
            (id, name, type, symbol, timeframe, params, metrics, status, owner, stage, stage_changed_at, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "S-NEAR-PAPER",
                "NEAR paper strategy",
                "obv_divergence_mr",
                "NEAR/USDT",
                "5m",
                json.dumps({}),
                json.dumps({}),
                "paper",
                "risk-manager",
                "paper",
                now,
                now,
                now,
            ),
        )

    assert "NEAR" in _active_coins()
