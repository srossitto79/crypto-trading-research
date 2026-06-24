"""Synchronous wrappers around ExchangeInterface for use in sync contexts.

This module provides convenient sync wrappers when you're in a sync context
but need to call the async exchange interface. Use when you can't make your
function async (e.g., for CLI tools, lambda callbacks, or legacy sync code).

For new code and async contexts, prefer calling the async interface directly.
"""

import asyncio
from typing import Any, Dict, List, Optional

from forven.exchange.hyperliquid_adapter import HyperliquidExchange
from forven.exchange.interface import (
    ExchangeInterface,
    Order,
    OrderResult,
    Position,
)


def _get_event_loop() -> asyncio.AbstractEventLoop:
    """Get or create an event loop for running async code in sync contexts."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is None:
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

    return loop


class SyncExchange:
    """Synchronous wrapper around ExchangeInterface.

    Use this when you're in a sync context but need to call the exchange.
    For async code, use the interface directly instead.
    """

    def __init__(self, exchange: Optional[ExchangeInterface] = None, testnet: bool = True):
        self.exchange = exchange or HyperliquidExchange(testnet=testnet)

    def get_account_value(self) -> float:
        """Get total account value in USD."""
        loop = _get_event_loop()
        return loop.run_until_complete(self.exchange.get_account_value())

    def get_positions(self) -> List[Position]:
        """Get all open positions."""
        loop = _get_event_loop()
        return loop.run_until_complete(self.exchange.get_positions())

    def get_open_orders(self, symbol: Optional[str] = None) -> List[Order]:
        """Get all open orders."""
        loop = _get_event_loop()
        return loop.run_until_complete(self.exchange.get_open_orders(symbol))

    def get_user_fills(
        self, symbol: Optional[str] = None, limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Get recent fills."""
        loop = _get_event_loop()
        return loop.run_until_complete(
            self.exchange.get_user_fills(symbol, limit)
        )

    def market_order(
        self,
        symbol: str,
        side: str,
        size: float,
        stop_loss_price: Optional[float] = None,
        take_profit_price: Optional[float] = None,
        **kwargs: Any,
    ) -> OrderResult:
        """Execute a market order."""
        loop = _get_event_loop()
        return loop.run_until_complete(
            self.exchange.market_order(
                symbol, side, size, stop_loss_price, take_profit_price, **kwargs
            )
        )

    def limit_order(
        self, symbol: str, side: str, size: float, price: float, **kwargs: Any
    ) -> OrderResult:
        """Execute a limit order."""
        loop = _get_event_loop()
        return loop.run_until_complete(
            self.exchange.limit_order(symbol, side, size, price, **kwargs)
        )

    def cancel_order(
        self, order_id: str, symbol: Optional[str] = None, **kwargs: Any
    ) -> bool:
        """Cancel an order."""
        loop = _get_event_loop()
        return loop.run_until_complete(
            self.exchange.cancel_order(order_id, symbol, **kwargs)
        )

    def close_position(self, symbol: str, **kwargs: Any) -> OrderResult:
        """Close a position."""
        loop = _get_event_loop()
        return loop.run_until_complete(
            self.exchange.close_position(symbol, **kwargs)
        )

    def place_protective_stop(
        self, symbol: str, size: float, trigger_price: float, **kwargs: Any
    ) -> OrderResult:
        """Place a stop-loss order."""
        loop = _get_event_loop()
        return loop.run_until_complete(
            self.exchange.place_protective_stop(symbol, size, trigger_price, **kwargs)
        )

    def place_take_profit(
        self, symbol: str, size: float, trigger_price: float, **kwargs: Any
    ) -> OrderResult:
        """Place a take-profit order."""
        loop = _get_event_loop()
        return loop.run_until_complete(
            self.exchange.place_take_profit(symbol, size, trigger_price, **kwargs)
        )

    def set_leverage(self, symbol: str, leverage: int) -> bool:
        """Set leverage."""
        loop = _get_event_loop()
        return loop.run_until_complete(
            self.exchange.set_leverage(symbol, leverage)
        )

    def get_all_mids(self) -> Dict[str, float]:
        """Get current mid prices."""
        loop = _get_event_loop()
        return loop.run_until_complete(self.exchange.get_all_mids())

    def get_candles(
        self, symbol: str, interval: str = "1h", limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Get OHLCV candles."""
        loop = _get_event_loop()
        return loop.run_until_complete(
            self.exchange.get_candles(symbol, interval, limit)
        )

    def health_check(self) -> bool:
        """Check if exchange is healthy."""
        loop = _get_event_loop()
        return loop.run_until_complete(self.exchange.health_check())

    def get_exchange_info(self) -> Dict[str, Any]:
        """Get exchange metadata."""
        loop = _get_event_loop()
        return loop.run_until_complete(self.exchange.get_exchange_info())


# Module-level convenience function
_default_sync_exchange: Optional[SyncExchange] = None


def get_sync_exchange(testnet: bool = True) -> SyncExchange:
    """Get or create the default sync exchange wrapper."""
    global _default_sync_exchange
    if _default_sync_exchange is None:
        _default_sync_exchange = SyncExchange(testnet=testnet)
    return _default_sync_exchange
