"""Startup catch-up planning for desktop-only data collection."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import pandas as pd

from axiom.dataeng.catalog import Catalog


@dataclass(frozen=True)
class CatchUpTask:
    source: str
    market: str
    symbol: str
    timeframe: str
    stream: str
    start_ts: str
    end_ts: str
    permanent: bool = False


class CatchUpPlanner:
    def __init__(self, catalog: Catalog | None = None) -> None:
        self.catalog = catalog or Catalog()

    def plan(self, *, now: datetime | None = None) -> list[CatchUpTask]:
        now_ts = _as_utc(now or datetime.now(timezone.utc))
        tasks: list[CatchUpTask] = []
        for row in self.catalog.list_coverage():
            stream = str(row.get("stream") or "")
            end_raw = row.get("end_ts")
            timeframe = str(row.get("timeframe") or "")
            if not end_raw or not timeframe:
                continue
            end_ts = _as_utc(end_raw)
            if stream in {"trades", "orderbook"}:
                tasks.append(_task_from_row(row, end_ts, now_ts, permanent=True))
                continue
            if stream != "candles":
                continue
            tf_delta = _timeframe_delta(timeframe)
            start_ts = end_ts + tf_delta
            # Only closed bars are catch-up candidates.
            latest_closed_start = _floor_to_timeframe(now_ts, tf_delta) - tf_delta
            if start_ts <= latest_closed_start:
                tasks.append(_task_from_row(row, start_ts, latest_closed_start, permanent=False))
        return tasks


def _task_from_row(row: dict[str, object], start_ts: pd.Timestamp, end_ts: pd.Timestamp, *, permanent: bool) -> CatchUpTask:
    return CatchUpTask(
        source=str(row.get("source") or ""),
        market=str(row.get("market") or ""),
        symbol=str(row.get("symbol") or ""),
        timeframe=str(row.get("timeframe") or ""),
        stream=str(row.get("stream") or ""),
        start_ts=_to_iso(start_ts),
        end_ts=_to_iso(end_ts),
        permanent=permanent,
    )


def _timeframe_delta(timeframe: str) -> pd.Timedelta:
    from axiom.data import _timeframe_to_ms

    return pd.Timedelta(milliseconds=_timeframe_to_ms(timeframe))


def _floor_to_timeframe(value: pd.Timestamp, delta: pd.Timedelta) -> pd.Timestamp:
    ts = _as_utc(value)
    seconds = delta.total_seconds()
    if seconds <= 0:
        return ts.floor("s")
    if seconds % 86400 == 0:
        return ts.floor(f"{int(seconds // 86400)}D")
    if seconds % 3600 == 0:
        return ts.floor(f"{int(seconds // 3600)}h")
    if seconds % 60 == 0:
        return ts.floor(f"{int(seconds // 60)}min")
    return ts.floor(f"{int(seconds)}s")


def _as_utc(value: object) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        return ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


def _to_iso(value: pd.Timestamp) -> str:
    return _as_utc(value).isoformat().replace("+00:00", "Z")
