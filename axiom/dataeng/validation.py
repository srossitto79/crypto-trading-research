"""Cross-source validation and resolution policies."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pandas as pd


ValidationPolicy = Literal["off", "flag", "median", "priority", "flag_priority"]


@dataclass(frozen=True)
class ValidationResult:
    frame: pd.DataFrame
    flags: pd.DataFrame


def validate_bars(
    frames: dict[str, pd.DataFrame],
    *,
    policy: ValidationPolicy = "flag_priority",
    tolerance_bps: float = 1.0,
    priority: list[str] | None = None,
) -> ValidationResult:
    if not frames:
        return ValidationResult(pd.DataFrame(), pd.DataFrame())
    priority = priority or list(frames)
    if policy == "off" or len(frames) == 1:
        source = next((item for item in priority if item in frames), next(iter(frames)))
        return ValidationResult(frames[source].copy(), _empty_flags())

    aligned = []
    for source, frame in frames.items():
        work = frame.copy()
        work["timestamp"] = pd.to_datetime(work["timestamp"], utc=True, errors="coerce")
        work["source"] = source
        aligned.append(work)
    stacked = pd.concat(aligned, ignore_index=True).dropna(subset=["timestamp"])

    flags = _divergence_flags(stacked, tolerance_bps)
    if policy == "median":
        resolved = _median_resolved(stacked)
    else:
        resolved = _priority_resolved(stacked, priority)
        if policy == "flag":
            resolved = resolved.merge(flags[["timestamp", "divergent"]], on="timestamp", how="left")
    return ValidationResult(resolved.reset_index(drop=True), flags.reset_index(drop=True))


def _divergence_flags(stacked: pd.DataFrame, tolerance_bps: float) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    tolerance = float(tolerance_bps) / 10_000.0
    for ts, group in stacked.groupby("timestamp", sort=True):
        closes = pd.to_numeric(group["close"], errors="coerce").dropna()
        if closes.empty:
            continue
        median = float(closes.median())
        max_deviation = 0.0 if median == 0 else float((closes.sub(median).abs() / abs(median)).max())
        rows.append(
            {
                "timestamp": ts,
                "divergent": max_deviation > tolerance,
                "max_deviation_bps": max_deviation * 10_000.0,
                "sources": sorted(group["source"].astype(str).unique().tolist()),
            }
        )
    return pd.DataFrame(rows) if rows else _empty_flags()


def _priority_resolved(stacked: pd.DataFrame, priority: list[str]) -> pd.DataFrame:
    priority_rank = {source: idx for idx, source in enumerate(priority)}
    work = stacked.copy()
    work["_rank"] = work["source"].map(priority_rank).fillna(len(priority_rank)).astype(int)
    work = work.sort_values(["timestamp", "_rank"]).drop_duplicates("timestamp", keep="first")
    return work.drop(columns=["_rank"]).reset_index(drop=True)


def _median_resolved(stacked: pd.DataFrame) -> pd.DataFrame:
    numeric = ["open", "high", "low", "close", "volume"]
    grouped = stacked.groupby("timestamp", sort=True)[numeric].median(numeric_only=True).reset_index()
    grouped["source"] = "median"
    return grouped


def _empty_flags() -> pd.DataFrame:
    return pd.DataFrame(columns=["timestamp", "divergent", "max_deviation_bps", "sources"])
