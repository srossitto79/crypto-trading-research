import pytest

from axiom.api_core import _normalize_trade_rows


def test_normalize_trade_rows_keeps_small_percent_point_returns():
    rows = [
        {
            "entry_time": "2026-01-01T00:00:00+00:00",
            "exit_time": "2026-01-01T01:00:00+00:00",
            "entry_price": 100.0,
            "exit_price": 100.229,
            "pnl": 22.9,
            "return_pct": 0.229,
        }
    ]

    [trade] = _normalize_trade_rows(rows)

    assert trade["return_pct"] == pytest.approx(0.229)
    assert trade["pnl"] == pytest.approx(22.9)


def test_normalize_trade_rows_repairs_legacy_ratio_shape():
    rows = [
        {
            "entry_time": "2026-01-01T00:00:00+00:00",
            "exit_time": "2026-01-01T01:00:00+00:00",
            "entry_price": 100.0,
            "exit_price": 101.0,
            "pnl": 0.0132,
            "return_pct": 0.0132,
        }
    ]

    [trade] = _normalize_trade_rows(rows)

    assert trade["return_pct"] == pytest.approx(1.32)
    assert trade["pnl"] == pytest.approx(132.0)
