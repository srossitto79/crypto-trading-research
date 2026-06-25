"""ETH 15m EMA trend template built on the certified EMA-cross family."""

from __future__ import annotations

from axiom.strategies.builtin.ema_cross import EMACrossStrategy

TYPE_NAME = "ema_cross_eth_15m"


class Eth15mEmaTrendStrategy(EMACrossStrategy):
    """Preset ETH 15m EMA trend strategy for pipeline experimentation."""

    @property
    def name(self) -> str:
        return "ETH 15m EMA Trend"

    @property
    def asset(self) -> str:
        return str(self.params.get("_asset") or "ETH")

    @property
    def strategy_type(self) -> str:
        return TYPE_NAME

    @property
    def default_params(self) -> dict:
        return {
            "_asset": "ETH",
            "timeframe": "15m",
            "ema_fast": 24,
            "ema_slow": 32,
            "ema_regime": 192,
            "adx_period": 14,
            "adx_min": 25,
            "leverage": 1.0,
        }

    def data_requirements(self) -> list[dict]:
        warmup = max(
            int(self.params.get("ema_fast", 24)),
            int(self.params.get("ema_slow", 32)),
            int(self.params.get("ema_regime", 192)),
            int(self.params.get("adx_period", 14)),
        )
        return [
            {
                "asset": self.asset,
                "exchange": "any",
                "timeframe": str(self.params.get("timeframe") or "15m"),
                "min_bars": max(warmup + 100, 500),
            }
        ]

    def describe(self) -> str:
        p = self.params
        return (
            f"ETH 15m trend template. Buys when EMA {p['ema_fast']} crosses above EMA {p['ema_slow']} "
            f"while price is above EMA {p['ema_regime']} and ADX({p['adx_period']}) is at least {p['adx_min']}. "
            "Exits on the bearish fast/slow EMA cross."
        )

    def parameter_space(self) -> dict:
        return {
            "ema_fast": (16, 32, 4),
            "ema_slow": (24, 48, 8),
            "ema_regime": (144, 240, 24),
            "adx_min": (20, 30, 5),
        }


STRATEGY_CLASS = Eth15mEmaTrendStrategy
STRATEGIES: list[tuple[str, type[Eth15mEmaTrendStrategy], dict]] = []
