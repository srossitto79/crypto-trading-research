"""Abstract interface for exchange operations. Decouples execution from Hyperliquid SDK."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class Position:
    """An open position on the exchange."""
    symbol: str
    side: str  # 'long' or 'short'
    size: float
    entry_price: float
    current_price: float
    leverage: int
    unrealized_pnl: float


@dataclass
class Order:
    """An open or recent order."""
    order_id: str
    symbol: str
    side: str  # 'buy'/'sell' or 'long'/'short'
    price: float
    size: float
    filled_size: float
    order_type: str  # 'market', 'limit', 'trigger_market', etc.
    timestamp: float
    status: str  # 'open', 'filled', 'cancelled', etc.


@dataclass
class OrderResult:
    """Result of an order submission."""
    success: bool
    order_id: Optional[str] = None
    error: Optional[str] = None
    raw_response: Optional[Dict[str, Any]] = None


@dataclass
class AccountSnapshot:
    """Current account state."""
    total_value: float
    free_collateral: float
    positions: List[Position]
    timestamp: float


@dataclass
class Candle:
    """OHLCV candlestick data."""
    timestamp: float
    open: float
    high: float
    low: float
    close: float
    volume: float


class ExchangeInterface(ABC):
    """
    Abstract interface for exchange operations.

    Implementations: HyperliquidExchange, MockExchange, CCXT adapters, etc.
    """

    # ======================== Account & Positions ========================

    @abstractmethod
    async def get_account_value(self) -> float:
        """Get total account value in USD."""
        pass

    @abstractmethod
    async def get_positions(self) -> List[Position]:
        """Get all open positions."""
        pass

    @abstractmethod
    async def get_open_orders(self, symbol: Optional[str] = None) -> List[Order]:
        """Get all open orders, optionally filtered by symbol."""
        pass

    @abstractmethod
    async def get_user_fills(
        self, symbol: Optional[str] = None, limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Get recent fills (executed trades)."""
        pass

    # ======================== Order Execution ========================

    @abstractmethod
    async def market_order(
        self,
        symbol: str,
        side: str,
        size: float,
        stop_loss_price: Optional[float] = None,
        take_profit_price: Optional[float] = None,
        **kwargs: Any,
    ) -> OrderResult:
        """
        Execute a market order.

        Args:
            symbol: Trading pair (e.g., 'BTC', 'ETH')
            side: 'long', 'short', 'buy', or 'sell'
            size: Position size
            stop_loss_price: Optional stop-loss trigger price
            take_profit_price: Optional take-profit trigger price
            **kwargs: Exchange-specific options
        """
        pass

    @abstractmethod
    async def limit_order(
        self,
        symbol: str,
        side: str,
        size: float,
        price: float,
        **kwargs: Any,
    ) -> OrderResult:
        """
        Execute a limit order.

        Args:
            symbol: Trading pair
            side: 'long', 'short', 'buy', or 'sell'
            size: Position size
            price: Limit price
            **kwargs: Exchange-specific options
        """
        pass

    @abstractmethod
    async def cancel_order(
        self, order_id: str, symbol: Optional[str] = None, **kwargs: Any
    ) -> bool:
        """Cancel an order by ID."""
        pass

    @abstractmethod
    async def close_position(self, symbol: str, **kwargs: Any) -> OrderResult:
        """Close an existing position (market order in opposite direction)."""
        pass

    # ======================== Risk Orders ========================

    @abstractmethod
    async def place_protective_stop(
        self, symbol: str, size: float, trigger_price: float, **kwargs: Any
    ) -> OrderResult:
        """
        Place a stop-loss order (protective stop).

        Triggers a market close when price hits trigger_price.
        """
        pass

    @abstractmethod
    async def place_take_profit(
        self, symbol: str, size: float, trigger_price: float, **kwargs: Any
    ) -> OrderResult:
        """
        Place a take-profit order.

        Triggers a market close when price hits trigger_price.
        """
        pass

    # ======================== Leverage & Risk ========================

    @abstractmethod
    async def set_leverage(self, symbol: str, leverage: int) -> bool:
        """Set leverage for a symbol."""
        pass

    # ======================== Market Data ========================

    @abstractmethod
    async def get_all_mids(self) -> Dict[str, float]:
        """
        Fetch current mid prices for all available symbols.

        Returns a dict mapping symbol -> mid price.
        """
        pass

    @abstractmethod
    async def get_candles(
        self, symbol: str, interval: str = "1h", limit: int = 100
    ) -> List[Dict[str, Any]]:
        """
        Fetch OHLCV candlesticks.

        Args:
            symbol: Trading pair
            interval: '1m', '5m', '1h', '4h', '1d', etc.
            limit: Number of candles to fetch

        Returns:
            List of OHLCV dicts with 'timestamp', 'open', 'high', 'low', 'close', 'volume'
        """
        pass

    # ======================== Health & Metadata ========================

    @abstractmethod
    async def health_check(self) -> bool:
        """Check if exchange is reachable and operational."""
        pass

    @abstractmethod
    async def get_exchange_info(self) -> Dict[str, Any]:
        """
        Fetch exchange metadata.

        Returns metadata about available symbols, precision, contract specs, etc.
        """
        pass
