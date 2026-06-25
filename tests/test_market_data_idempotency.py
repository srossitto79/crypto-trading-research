"""H-D2: market_data_history INSERT OR IGNORE returns accurate counts and
logs duplicates for observability."""

from __future__ import annotations

import logging

import pytest

from axiom.db import init_db, get_db
from axiom.market_data_collector import (
    METRIC_FUNDING_RATE,
    _store_batch,
    _store_data_point,
)


@pytest.fixture(autouse=True)
def _ensure_db():
    init_db()


def _clear(asset: str):
    with get_db() as conn:
        conn.execute("DELETE FROM market_data_history WHERE asset = ?", (asset,))


def test_store_data_point_reports_insert_success():
    _clear("TESTCOIN")
    assert _store_data_point("TESTCOIN", METRIC_FUNDING_RATE, 0.0001, 1_700_000_000_000) is True


def test_store_data_point_reports_duplicate_skip(caplog):
    _clear("TESTCOIN2")
    ts = 1_700_000_001_000
    assert _store_data_point("TESTCOIN2", METRIC_FUNDING_RATE, 0.0001, ts) is True
    with caplog.at_level(logging.DEBUG, logger="axiom.market_data_collector"):
        ok = _store_data_point("TESTCOIN2", METRIC_FUNDING_RATE, 0.0002, ts)
    assert ok is False
    assert any("duplicate skipped" in r.getMessage() for r in caplog.records)


def test_store_batch_returns_inserted_count_not_row_count(caplog):
    _clear("TESTCOIN3")
    rows = [
        ("TESTCOIN3", METRIC_FUNDING_RATE, 0.0001, "2026-04-15T00:00:00+00:00", 1_700_000_100_000, "test", None),
        ("TESTCOIN3", METRIC_FUNDING_RATE, 0.0002, "2026-04-15T00:15:00+00:00", 1_700_000_200_000, "test", None),
    ]
    assert _store_batch(rows) == 2

    # Overlap one row, add one new
    overlap = [
        ("TESTCOIN3", METRIC_FUNDING_RATE, 0.0009, "2026-04-15T00:15:00+00:00", 1_700_000_200_000, "test", None),
        ("TESTCOIN3", METRIC_FUNDING_RATE, 0.0003, "2026-04-15T00:30:00+00:00", 1_700_000_300_000, "test", None),
    ]
    with caplog.at_level(logging.INFO, logger="axiom.market_data_collector"):
        inserted = _store_batch(overlap)
    assert inserted == 1  # only the new row was added
    assert any("1 duplicates ignored" in r.getMessage() for r in caplog.records)


def test_store_batch_empty_is_noop():
    assert _store_batch([]) == 0
