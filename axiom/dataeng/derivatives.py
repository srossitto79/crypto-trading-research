"""Derived Tier-1 series helpers."""

from __future__ import annotations

import pandas as pd


def perp_spot_basis(perp: pd.DataFrame, spot: pd.DataFrame) -> pd.DataFrame:
    left = _price_frame(perp, "perp_close")
    right = _price_frame(spot, "spot_close")
    merged = pd.merge_asof(left, right, on="timestamp", direction="backward")
    merged["basis"] = merged["perp_close"] - merged["spot_close"]
    merged["basis_pct"] = merged["basis"] / merged["spot_close"].replace(0, pd.NA)
    return merged[["timestamp", "perp_close", "spot_close", "basis", "basis_pct"]]


def aggregate_open_interest(frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    pieces = []
    for source, frame in frames.items():
        work = frame[["timestamp", "open_interest"]].copy()
        work["timestamp"] = pd.to_datetime(work["timestamp"], utc=True, errors="coerce")
        work["source"] = source
        pieces.append(work)
    if not pieces:
        return pd.DataFrame(columns=["timestamp", "open_interest"])
    stacked = pd.concat(pieces, ignore_index=True).dropna(subset=["timestamp"])
    return stacked.groupby("timestamp", as_index=False)["open_interest"].sum()


def _price_frame(frame: pd.DataFrame, column: str) -> pd.DataFrame:
    out = frame[["timestamp", "close"]].copy()
    out["timestamp"] = pd.to_datetime(out["timestamp"], utc=True, errors="coerce")
    out = out.dropna(subset=["timestamp"]).sort_values("timestamp")
    return out.rename(columns={"close": column})
