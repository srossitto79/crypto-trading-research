"""BaseStrategy - the interface all strategies implement."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Literal

import pandas as pd


TradeMode = Literal["long_only", "short_only", "both"]


class ParamAccessor:
    """Compatibility wrapper for generated strategies that use ``self.p``."""

    def __init__(self, params: dict):
        self._params = params

    def __call__(self, key: str, default=None):
        return self._params.get(key, default)

    def __getitem__(self, key: str):
        return self._params[key]

    def __getattr__(self, key: str):
        try:
            return self._params[key]
        except KeyError as exc:
            raise AttributeError(key) from exc

    def get(self, key: str, default=None):
        return self._params.get(key, default)

    def items(self):
        return self._params.items()

    def keys(self):
        return self._params.keys()

    def values(self):
        return self._params.values()

    def __contains__(self, key: object) -> bool:
        return key in self._params


def _normalize_direction(value) -> str:
    """Map the many direction spellings agent code emits onto ``long``/``short``.

    Agent templates ported from backtrader/backtesting.py use ``buy``/``sell`` (or
    ``side=``) instead of Axiom's ``long``/``short`` ``direction``. Normalize them
    so a strategy never silently trades the wrong way (or breaks downstream code
    that branches on ``direction``)."""
    text = str(value if value is not None else "long").strip().lower()
    if text in {"buy", "long", "bull", "bullish"}:
        return "long"
    if text in {"sell", "short", "bear", "bearish"}:
        return "short"
    return text or "long"


@dataclass
class Signal:
    """Standardized signal output from any strategy."""

    entry_signal: bool = False
    exit_signal: bool = False
    price: float = 0.0
    direction: str = "long"
    confidence: float = 0.0
    indicators: dict = field(default_factory=dict)
    regime_tag: str | None = None
    # Compatibility alias: agent code ported from other frameworks passes
    # ``side=`` instead of ``direction=``. When set it overrides direction.
    side: str | None = None

    def __post_init__(self) -> None:
        if self.side is not None:
            self.direction = self.side
        self.direction = _normalize_direction(self.direction)

    @classmethod
    def from_condition(cls, condition, *args, **kwargs) -> "Signal":
        """Compatibility constructor for generated strategy code.

        Some agent templates build a signal from boolean conditions instead of
        constructing ``Signal`` directly. ``generate_signal`` is evaluated on
        the current bar/window, so Series inputs are collapsed to their latest
        value.
        """
        df = kwargs.pop("df", None)
        exit_condition = kwargs.pop("exit_condition", kwargs.pop("exit_signal", False))
        if args:
            first = args[0]
            if isinstance(first, pd.DataFrame):
                df = first
            else:
                exit_condition = first
                if len(args) > 1 and isinstance(args[1], pd.DataFrame):
                    df = args[1]

        price = kwargs.pop("price", None)
        if price is None and isinstance(df, pd.DataFrame) and "close" in df.columns:
            price = df["close"]

        return cls(
            entry_signal=_latest_bool(condition),
            exit_signal=_latest_bool(exit_condition),
            price=_latest_float(price, 0.0),
            direction=str(_latest_value(kwargs.pop("direction", "long"), "long") or "long"),
            confidence=_latest_float(kwargs.pop("confidence", 1.0), 1.0),
            indicators=kwargs.pop("indicators", {}) or {},
            regime_tag=_latest_value(kwargs.pop("regime_tag", None), None),
        )

    def to_dict(self) -> dict:
        """Convert to the legacy dict format used by manage_positions()."""
        d = {
            "price": self.price,
            "entry_signal": self.entry_signal,
            "exit_signal": self.exit_signal,
            "direction": self.direction,
            **self.indicators,
        }
        return d


# Sentinel instances — strategies can return these instead of constructing Signal()
Signal.HOLD = Signal()
Signal.LONG = Signal(entry_signal=True, direction="long")
Signal.SHORT = Signal(entry_signal=True, direction="short")

# Backtrader / backtesting.py-style aliases for agent-generated code. Axiom's real
# model is (entry_signal/exit_signal + direction); these map onto it so ported
# templates using BUY/SELL/EXIT_* don't fail with AttributeError. (Previously the
# brain reported "Signal.BUY/SELL/EXIT_SHORT doesn't exist" against this API.)
Signal.BUY = Signal.LONG
Signal.SELL = Signal.SHORT
Signal.EXIT = Signal(exit_signal=True)
Signal.EXIT_LONG = Signal(exit_signal=True, direction="long")
Signal.EXIT_SHORT = Signal(exit_signal=True, direction="short")
# "Go flat" == close any open position. Maps to a plain exit.
Signal.FLAT = Signal(exit_signal=True)

# Some agent code imports a (hallucinated) ``SignalType`` enum. Alias it to Signal
# so both ``from axiom.strategies.base import SignalType`` and ``SignalType.LONG`` /
# ``SignalType.BUY`` resolve to the real sentinels above.
SignalType = Signal


def _latest_value(value, default=None):
    """Return a scalar latest value from common pandas/numpy containers."""
    if value is None:
        return default
    try:
        if isinstance(value, pd.Series):
            clean = value.dropna()
            if clean.empty:
                return default
            return clean.iloc[-1]
        if isinstance(value, pd.DataFrame):
            if value.empty:
                return default
            return value.iloc[-1]
    except Exception:
        return default
    return value


def _latest_bool(value, default: bool = False) -> bool:
    latest = _latest_value(value, default)
    try:
        return bool(latest)
    except Exception:
        return default


def _latest_float(value, default: float = 0.0) -> float:
    latest = _latest_value(value, default)
    try:
        return float(latest)
    except Exception:
        return default


@dataclass
class DirectionalSignals:
    """Vectorized directional signal payload aligned to a bar index."""

    long_entries: pd.Series
    long_exits: pd.Series
    short_entries: pd.Series
    short_exits: pd.Series

    @classmethod
    def empty(cls, index: pd.Index) -> "DirectionalSignals":
        base = pd.Series(False, index=index, dtype=bool)
        return cls(
            long_entries=base.copy(),
            long_exits=base.copy(),
            short_entries=base.copy(),
            short_exits=base.copy(),
        )


class _FlatPosition:
    """Benign stand-in for agent code that reads ``self.position``.

    Axiom strategies are STATELESS: open-position / PnL state lives in the backtest
    and execution engines, not the Strategy instance. Agent templates ported from
    backtrader/backtesting.py routinely read ``self.position`` (``self.position.size``,
    ``if self.position:``, ``self.position == 0``). Rather than crash with
    AttributeError (the reported LiqAvg-family bug), expose a flat sentinel that
    reads as "no position" in all the common forms."""

    size = 0.0
    price = 0.0

    def __bool__(self) -> bool:
        return False

    def __len__(self) -> int:
        return 0

    def __float__(self) -> float:
        return 0.0

    def __eq__(self, other: object) -> bool:
        try:
            return float(other) == 0.0  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return NotImplemented

    __hash__ = None  # type: ignore[assignment]


class BaseStrategy(ABC):
    """Base class for all trading strategies.

    Every strategy must implement:
    - generate_signal(df) -> Signal
    - metadata properties (name, asset, strategy_type, default_params)

    Optional overrides:
    - calculate_position_size(signal, account_equity) -> float
    - get_stop_loss(signal) -> float | None
    - generate_signals(df) -> tuple[pd.Series, pd.Series] | DirectionalSignals
    - parameter_space() -> dict  (for Phase 4 optimization)
    """

    def __init__(self, strategy_id: str, params: dict | None = None):
        self.strategy_id = strategy_id
        default_params = self.default_params
        if callable(default_params):
            default_params = default_params()
        if default_params is None:
            default_params = {}
        if not isinstance(default_params, dict):
            raise TypeError("default_params must resolve to a dict")
        self.params = {**default_params, **(params or {})}
        self.p = ParamAccessor(self.params)
        # Flat-position sentinel so agent code that reads self.position (ported from
        # stateful frameworks) degrades to "no position" instead of crashing. A
        # subclass that genuinely tracks its own position can just reassign it.
        self.position = _FlatPosition()

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def asset(self) -> str: ...

    @property
    @abstractmethod
    def strategy_type(self) -> str: ...

    @property
    @abstractmethod
    def default_params(self) -> dict: ...

    @abstractmethod
    def generate_signal(self, df: pd.DataFrame) -> Signal:
        """Run strategy logic on OHLCV data and return a Signal."""
        ...

    def generate_signals(
        self,
        df: pd.DataFrame,
    ) -> tuple[pd.Series, pd.Series] | DirectionalSignals | None:
        """Optional vectorized signal path for high-performance backtesting.

        Returns:
            Either:
            - Tuple[entry_signals, exit_signals] aligned to ``df.index`` for
              legacy long/short-single-side paths.
            - DirectionalSignals for explicit dual-side execution.
            Return ``None`` to use the default per-bar ``generate_signal`` loop.
        """
        return None

    @property
    def supported_trade_modes(self) -> set[TradeMode]:
        """Backtest/live trade modes the strategy can safely support."""
        return {"long_only"}

    @property
    def mirror_short_safe(self) -> bool:
        """Whether a long-side strategy can be auto-mirrored into short_only."""
        return False

    def calculate_position_size(self, signal: Signal, account_equity: float) -> float:
        """Default: use params risk_pct. Override for custom sizing."""
        risk_pct = self.params.get("risk_pct", 0.01)
        return account_equity * risk_pct / signal.price if signal.price > 0 else 0

    def get_stop_loss(self, signal: Signal) -> float | None:
        """Default: None (use trailing stop in daemon). Override for ATR-based etc."""
        return None

    def data_requirements(self) -> list[dict]:
        """Declare what data sources this strategy needs for backtesting.

        Override in subclasses that need non-standard data (e.g., multi-exchange,
        funding rates, or alternative assets).

        Returns a list of requirement dicts, each with:
            - asset: str (e.g., "BTC")
            - exchange: str (e.g., "binance", "hyperliquid")
            - timeframe: str (e.g., "1h")
            - min_bars: int (minimum bars needed)

        Default: single asset from self.asset on any available exchange.
        """
        return [{"asset": self.asset, "exchange": "any", "timeframe": "1h", "min_bars": 720}]

    def parameter_space(self) -> dict:
        """Override to declare optimizable parameter ranges.

        Returns: {"param_name": (min, max, step), ...}
        """
        return {}

    @property
    def compatible_regimes(self) -> set[str]:
        """Regimes this strategy is allowed to trade in. Override per-class."""
        return set()

    def describe(self) -> str:
        """Return a plain-English description of what this strategy does.

        Override in subclasses to produce strategy-specific descriptions
        using actual parameter values.
        """
        return f"{self.strategy_type} strategy on {self.asset}"

    def to_dict(self) -> dict:
        """Serialize for scanner/backtest compatibility with legacy format."""
        return {
            "name": self.name,
            "asset": self.asset,
            "type": self.strategy_type,
            "params": self.params,
            "description": self.describe(),
        }
