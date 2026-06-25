"""Tier-2 microstructure storage, reads, and retention."""

from __future__ import annotations

import os
from pathlib import Path

import duckdb
import pandas as pd

from axiom import config as AXIOM_config
from axiom.dataeng.identity import to_ref


def micro_root() -> Path:
    return AXIOM_config.AXIOM_HOME / "data" / "micro"


def write_micro_rows(
    stream: str,
    symbol: str,
    rows: pd.DataFrame,
    *,
    source: str = "binance",
    root: Path | None = None,
) -> list[Path]:
    frame = rows.copy()
    if frame.empty:
        return []
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True, errors="coerce")
    frame = frame.dropna(subset=["timestamp"])
    if frame.empty:
        return []

    ref = to_ref(symbol, source=source)
    base = (root or micro_root()) / stream / f"source={ref.source}" / ref.to_fs()
    written: list[Path] = []
    for date, group in frame.groupby(frame["timestamp"].dt.strftime("%Y-%m-%d")):
        partition = base / f"date={date}"
        partition.mkdir(parents=True, exist_ok=True)
        path = partition / f"part-{pd.Timestamp.utcnow().value}.parquet"
        tmp = Path(str(path) + ".tmp")
        group.to_parquet(tmp, index=False)
        os.replace(str(tmp), str(path))
        written.append(path)
    return written


def read_micro_rows(
    stream: str,
    symbol: str,
    *,
    start: object | None = None,
    end: object | None = None,
    source: str = "binance",
    root: Path | None = None,
) -> pd.DataFrame:
    ref = to_ref(symbol, source=source)
    pattern = (root or micro_root()) / stream / f"source={ref.source}" / ref.to_fs() / "date=*" / "*.parquet"
    if not pattern.parent.parent.exists():
        return pd.DataFrame()
    predicates: list[str] = []
    params: list[object] = [str(pattern)]
    if start is not None:
        predicates.append("timestamp >= ?")
        params.append(_as_utc(start))
    if end is not None:
        predicates.append("timestamp <= ?")
        params.append(_as_utc(end))
    where = f" WHERE {' AND '.join(predicates)}" if predicates else ""
    with duckdb.connect(":memory:") as con:
        return con.execute(f"SELECT * FROM read_parquet(?){where} ORDER BY timestamp", params).fetchdf()


def rollup_trades_per_minute(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(columns=["timestamp", "buy_volume", "sell_volume", "cvd", "trade_imbalance"])
    work = frame.copy()
    work["timestamp"] = pd.to_datetime(work["timestamp"], utc=True, errors="coerce")
    work["minute"] = work["timestamp"].dt.floor("min")
    side = work["side"] if "side" in work.columns else pd.Series(["buy"] * len(work), index=work.index)
    work["signed_volume"] = pd.to_numeric(work["amount"], errors="coerce").fillna(0.0)
    work.loc[side.astype(str).str.lower().eq("sell"), "signed_volume"] *= -1
    grouped = work.groupby("minute", as_index=False).agg(
        buy_volume=("amount", lambda s: float(s[work.loc[s.index, "signed_volume"] >= 0].sum())),
        sell_volume=("amount", lambda s: float(s[work.loc[s.index, "signed_volume"] < 0].sum())),
        cvd=("signed_volume", "sum"),
    )
    total = grouped["buy_volume"] + grouped["sell_volume"]
    grouped["trade_imbalance"] = (grouped["buy_volume"] - grouped["sell_volume"]) / total.replace(0, pd.NA)
    return grouped.rename(columns={"minute": "timestamp"})


def _as_utc(value: object) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        return ts.tz_localize("UTC")
    return ts.tz_convert("UTC")
