"""Tests for the auto Data Engine catch-up: the bounded plan executor, the wired
settings, and the scheduled job registration."""

from types import SimpleNamespace

import axiom.api_domains.data as data_domain
from axiom.dataeng.settings import (
    DataEngineSettings,
    default_data_engine_settings_payload,
    merge_data_engine_settings_payload,
)


def _task(symbol: str, timeframe: str, stream: str = "candles"):
    return SimpleNamespace(
        source="binance",
        market="futures",
        symbol=symbol,
        timeframe=timeframe,
        stream=stream,
        start_ts=0,
        end_ts=0,
        permanent=False,
    )


class _FakePlanner:
    """Stands in for CatchUpPlanner — returns a fixed task list regardless of args."""

    tasks: list = []

    def __init__(self, *args, **kwargs):
        pass

    def plan(self, *args, **kwargs):
        return list(type(self).tasks)


class _FakeCatalog:
    """Stands in for Catalog — records scan_lake calls without touching DuckDB."""

    scan_calls = 0

    def __init__(self, *args, **kwargs):
        pass

    def scan_lake(self, *args, **kwargs):
        type(self).scan_calls += 1
        return []


def _patch_executor(monkeypatch, tasks, backfill):
    monkeypatch.setattr("axiom.dataeng.catchup.CatchUpPlanner", _FakePlanner)
    _FakePlanner.tasks = tasks
    monkeypatch.setattr("axiom.dataeng.catalog.Catalog", _FakeCatalog)
    _FakeCatalog.scan_calls = 0
    monkeypatch.setattr("axiom.data.backfill_ohlcv_gaps", backfill)
    # The action log touches the DB; keep the unit test isolated from it.
    monkeypatch.setattr("axiom.data._log_data_action", lambda *a, **k: None)
    # Stall-deprioritization state is process-global; isolate each test.
    data_domain._catchup_stalled.clear()


def test_executor_bounds_batch_to_max_tasks(monkeypatch):
    """Only `max_tasks` candle series are refreshed per run, but totals reflect
    the full plan so the UI can show the remaining backlog."""
    tasks = [_task(f"SYM{i}-USDT", "1h") for i in range(30)]
    tasks += [_task("BTC-USDT", "1h", stream="trades")]  # non-candle, must be ignored

    calls: list = []

    def fake_backfill(symbol, timeframe, **kwargs):
        calls.append((symbol, timeframe))
        return {"bars_added": 5, "no_recent_data": False}

    _patch_executor(monkeypatch, tasks, fake_backfill)

    result = data_domain.execute_data_engine_catchup(max_tasks=12)

    assert result["planned_total"] == 31
    assert result["candle_total"] == 30  # trades task excluded
    assert result["executed"] == 12  # bounded
    assert len(calls) == 12
    assert result["rows_added"] == 60  # 12 * 5
    assert result["failed"] == 0


def test_executor_counts_stalled_series_as_failed(monkeypatch):
    """A series that adds no bars AND can't fetch newer data is a real failure,
    not a silent green success."""
    tasks = [_task("GOOD-USDT", "1h"), _task("DEAD-USDT", "1h")]

    def fake_backfill(symbol, timeframe, **kwargs):
        if symbol == "DEAD-USDT":
            return {"bars_added": 0, "no_recent_data": True}  # delisted / stalled
        return {"bars_added": 3, "no_recent_data": False}

    _patch_executor(monkeypatch, tasks, fake_backfill)

    result = data_domain.execute_data_engine_catchup(max_tasks=10)

    assert result["executed"] == 2
    assert result["failed"] == 1
    assert result["rows_added"] == 3


def test_executor_cap_is_respected(monkeypatch):
    """The hard cap protects the scheduler even if a huge max_tasks is passed."""
    tasks = [_task(f"S{i}-USDT", "1h") for i in range(80)]
    _patch_executor(monkeypatch, tasks, lambda s, t, **k: {"bars_added": 1})

    result = data_domain.execute_data_engine_catchup(max_tasks=999, cap=50)
    assert result["executed"] == 50


def test_executor_rescans_lake_before_planning(monkeypatch):
    """Audit B-18: scan_lake is the sole writer of series_coverage, so the
    scheduled job must refresh coverage before planning or it re-executes the
    same alphabetically-first batch forever."""
    _patch_executor(monkeypatch, [_task("BTC-USDT", "1h")], lambda s, t, **k: {"bars_added": 1})

    data_domain.execute_data_engine_catchup(max_tasks=5)

    assert _FakeCatalog.scan_calls == 1


def test_executor_deprioritizes_stalled_series(monkeypatch):
    """A permanently-stalled series (delisted/unfillable) must rotate to the
    back of the queue so it can't monopolize every bounded batch."""
    tasks = [_task("DEAD-USDT", "1h"), _task("GOOD-USDT", "1h")]
    calls: list[str] = []

    def fake_backfill(symbol, timeframe, **kwargs):
        calls.append(symbol)
        if symbol == "DEAD-USDT":
            return {"bars_added": 0, "no_recent_data": True}
        return {"bars_added": 2, "no_recent_data": False}

    _patch_executor(monkeypatch, tasks, fake_backfill)

    # Run 1: alphabetical head (DEAD) is attempted and stalls.
    first = data_domain.execute_data_engine_catchup(max_tasks=1)
    assert calls == ["DEAD-USDT"]
    assert first["failed"] == 1

    # Run 2: same plan, but the stalled head is deprioritized — the batch
    # advances to the next series instead of retrying DEAD forever.
    second = data_domain.execute_data_engine_catchup(max_tasks=1)
    assert calls == ["DEAD-USDT", "GOOD-USDT"]
    assert second["failed"] == 0


def test_catchup_advances_past_completed_batch(monkeypatch, tmp_path):
    """End-to-end drain check with a REAL catalog + planner: after a run
    backfills a series, the next run must see the new coverage (via the lake
    rescan) and not re-plan/re-execute the same series."""
    import pandas as pd

    lake = tmp_path / "data"
    series_dir = lake / "ohlcv" / "BTC-USDT"
    series_dir.mkdir(parents=True)
    parquet_path = series_dir / "1h.parquet"

    def _write_bars(end: pd.Timestamp, periods: int = 48) -> None:
        idx = pd.date_range(end=end, periods=periods, freq="h", tz="UTC")
        pd.DataFrame(
            {
                "timestamp": idx,
                "open": 1.0,
                "high": 1.1,
                "low": 0.9,
                "close": 1.0,
                "volume": 5.0,
            }
        ).to_parquet(parquet_path, index=False)

    now = pd.Timestamp.now(tz="UTC").floor("h")
    _write_bars(now - pd.Timedelta(days=3))  # series is 3 days behind

    # Point the real Catalog/planner at an isolated lake + duckdb file.
    monkeypatch.setattr("axiom.dataeng.catalog.default_data_root", lambda: lake)
    monkeypatch.setattr(
        "axiom.dataeng.catalog.default_catalog_path", lambda: tmp_path / "catalog.duckdb"
    )
    monkeypatch.setattr("axiom.data._log_data_action", lambda *a, **k: None)
    data_domain._catchup_stalled.clear()

    backfills: list[str] = []

    def fake_backfill(symbol, timeframe, **kwargs):
        backfills.append(symbol)
        # Bring the series fully current (through the in-progress hour) so the
        # assertion can't flake if the wall clock crosses an hour boundary
        # between the two executor runs.
        _write_bars(now + pd.Timedelta(hours=1))
        return {"bars_added": 71, "no_recent_data": False}

    monkeypatch.setattr("axiom.data.backfill_ohlcv_gaps", fake_backfill)

    first = data_domain.execute_data_engine_catchup(max_tasks=5)
    assert backfills == ["BTC-USDT"]
    assert first["executed"] == 1
    assert first["rows_added"] == 71

    # Second run: the rescan picks up the post-backfill parquet bounds, so the
    # completed series drains from the plan instead of re-running forever.
    second = data_domain.execute_data_engine_catchup(max_tasks=5)
    assert backfills == ["BTC-USDT"], "completed series was re-executed"
    assert second["executed"] == 0
    assert second["candle_total"] == 0


def test_settings_defaults_and_roundtrip():
    """The wired catch-up knobs default sensibly and survive a merge that touches
    only unrelated keys."""
    defaults = DataEngineSettings()
    assert defaults.auto_catchup_enabled is True
    assert defaults.auto_catchup_batch == 12

    payload = default_data_engine_settings_payload()
    assert payload["auto_catchup_enabled"] is True
    assert payload["auto_catchup_batch"] == 12

    # Setting an unrelated key must not drop the catch-up fields, and an explicit
    # override must be preserved.
    merged = merge_data_engine_settings_payload(
        {"enabled": True, "auto_catchup_enabled": False, "auto_catchup_batch": 25}
    )
    assert merged["auto_catchup_enabled"] is False
    assert merged["auto_catchup_batch"] == 25
    assert merged["enabled"] is True


def test_catchup_job_is_a_registered_default():
    """The job id must be in the default set, else reconcile_AXIOM_jobs would
    delete it as a stale Axiom- row on every startup."""
    from axiom import scheduler

    assert "Axiom-data-engine-catchup" in scheduler._DEFAULT_JOB_IDS


def test_catchup_runs_in_background_pool():
    """The network-heavy catch-up must run in the concurrent background pool, not
    inline — inline, a slow/hung run blocks the due-job loop and holds up every
    other inline job behind it (scanner, phantom recovery, validation cycle)."""
    from axiom import scheduler

    assert "data_engine_catchup" in scheduler._BACKGROUND_SCHEDULER_JOB_KINDS


def test_catchup_deadline_stops_batch_gracefully(monkeypatch):
    """A wall-clock deadline stops the batch with partial progress instead of
    overrunning the scheduler timeout into an unkillable zombie thread that holds
    the scheduler lock (the bug this guards against)."""
    tasks = [_task(f"X{i}-USDT", "1h") for i in range(5)]
    _patch_executor(monkeypatch, tasks, lambda s, t: {"bars_added": 1})

    # deadline 0 -> the pre-task check fires before the first task runs.
    out = data_domain.execute_data_engine_catchup(max_tasks=5, deadline_seconds=0.0)
    assert out["executed"] == 0
    assert out["deadline_hit"] is True

    # No deadline (manual HTTP path) processes the whole bounded batch.
    out2 = data_domain.execute_data_engine_catchup(max_tasks=5)
    assert out2["executed"] == 5
    assert out2["deadline_hit"] is False
