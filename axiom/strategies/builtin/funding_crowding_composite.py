"""Funding-Crowding Composite Mean Reversion strategy — S076."""

import pandas as pd

from axiom.strategies.base import BaseStrategy, Signal

TYPE_NAME = "funding_crowding_composite"


class FundingCrowdingCompositeStrategy(BaseStrategy):

    @property
    def name(self) -> str:
        return f"Funding-Crowding Composite ({self.asset})"

    @property
    def asset(self) -> str:
        return self.params.get("_asset", "BTC")

    @property
    def strategy_type(self) -> str:
        return TYPE_NAME

    @property
    def default_params(self) -> dict:
        return {
            "funding_zscore": 2.0,
            "ls_extreme": 1.8,
            "liq_imbalance_threshold": 0.3,
            "min_conditions": 2,
            "lookback": 24,
            "leverage": 2.0,
        }

    @property
    def compatible_regimes(self) -> set[str]:
        return {"RANGE_BOUND"}

    def describe(self) -> str:
        p = self.params
        return (
            f"Multi-signal mean-reversion requiring {p.get('min_conditions', 2)}+ of 3 conditions: "
            f"funding Z-score > {p.get('funding_zscore', 2.0)}\u03c3, "
            f"ls_ratio extreme > {p.get('ls_extreme', 1.8)}, "
            f"liq_imbalance > {p.get('liq_imbalance_threshold', 0.3)}."
        )

    def generate_signal(self, df: pd.DataFrame) -> Signal:
        p = self.params
        close = df["close"]
        curr_close = float(close.iloc[-1])
        lookback = int(p.get("lookback", 24))
        min_cond = int(p.get("min_conditions", 2))

        conditions_met = 0
        direction_votes = {"long": 0, "short": 0}

        # Condition 1: Funding rate Z-score
        funding_z = 0.0
        if "funding_rate" in df.columns:
            fr = df["funding_rate"]
            rolling_mean = fr.rolling(lookback).mean()
            rolling_std = fr.rolling(lookback).std()
            z = (fr - rolling_mean) / rolling_std.replace(0, float("nan"))
            funding_z = float(z.iloc[-1]) if pd.notna(z.iloc[-1]) else 0.0
            if abs(funding_z) > float(p.get("funding_zscore", 2.0)):
                conditions_met += 1
                # Positive funding = longs paying -> fade with short
                if funding_z > 0:
                    direction_votes["short"] += 1
                else:
                    direction_votes["long"] += 1

        # Condition 2: LS ratio extreme
        ls_val = 0.0
        if "ls_ratio" in df.columns:
            ls_val = float(df["ls_ratio"].iloc[-1]) if pd.notna(df["ls_ratio"].iloc[-1]) else 1.0
            ls_extreme = float(p.get("ls_extreme", 1.8))
            if ls_val > ls_extreme:
                conditions_met += 1
                direction_votes["short"] += 1  # Too many longs -> fade
            elif ls_val < 1.0 / ls_extreme:
                conditions_met += 1
                direction_votes["long"] += 1  # Too many shorts -> fade

        # Condition 3: Liquidation imbalance
        liq_imbalance = 0.0
        if "long_liq_usd" in df.columns and "short_liq_usd" in df.columns:
            long_liq = float(df["long_liq_usd"].iloc[-1]) if pd.notna(df["long_liq_usd"].iloc[-1]) else 0.0
            short_liq = float(df["short_liq_usd"].iloc[-1]) if pd.notna(df["short_liq_usd"].iloc[-1]) else 0.0
            total_liq = long_liq + short_liq
            if total_liq > 0:
                liq_imbalance = abs(long_liq - short_liq) / total_liq
            threshold = float(p.get("liq_imbalance_threshold", 0.3))
            if liq_imbalance > threshold:
                conditions_met += 1
                if long_liq > short_liq:
                    direction_votes["long"] += 1  # Longs liquidated -> bounce
                else:
                    direction_votes["short"] += 1

        entry = conditions_met >= min_cond
        direction = "long" if direction_votes["long"] >= direction_votes["short"] else "short"
        confidence = min(conditions_met / 3.0, 1.0)

        exit_signal = conditions_met == 0

        return Signal(
            entry_signal=bool(entry),
            exit_signal=bool(exit_signal),
            price=round(curr_close, 4),
            direction=direction,
            confidence=round(confidence, 4),
            indicators={
                "funding_zscore": round(funding_z, 4),
                "ls_ratio": round(ls_val, 4),
                "liq_imbalance": round(liq_imbalance, 4),
                "conditions_met": conditions_met,
            },
        )


STRATEGY_CLASS = FundingCrowdingCompositeStrategy

STRATEGIES = [
    ("S076-FCC-BTC", FundingCrowdingCompositeStrategy, {"_asset": "BTC"}),
    ("S077-FCC-ETH", FundingCrowdingCompositeStrategy, {"_asset": "ETH"}),
]
