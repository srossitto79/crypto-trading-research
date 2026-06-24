"""MockExchange - in-memory exchange implementation for testing and paper trading."""

import logging
import uuid
from typing import Any, Dict, List, Optional

from forven.exchange.interface import (
    ExchangeInterface,
    Order,
    OrderResult,
    Position,
)

log = logging.getLogger("forven.exchange.mock")


class MockExchange(ExchangeInterface):
    """
    Mock exchange for testing and paper trading simulation.

    Maintains in-memory state: positions, orders, fills, account value.
    Executes orders instantly at configurable prices.
    """

    def __init__(self, initial_balance: float = 10000.0):
        self.account_value = initial_balance
        self.positions: Dict[str, Position] = {}
        self.orders: Dict[str, Order] = {}
        self.fills: List[Dict[str, Any]] = []
        self.mid_prices: Dict[str, float] = {}
        self.leverage: Dict[str, int] = {}

    # ======================== Account & Positions ========================

    async def get_account_value(self) -> float:
        """Return current account value."""
        return self.account_value

    async def get_positions(self) -> List[Position]:
        """Return all open positions."""
        return list(self.positions.values())

    async def get_open_orders(self, symbol: Optional[str] = None) -> List[Order]:
        """Return open orders, optionally filtered by symbol."""
        orders = [o for o in self.orders.values() if o.status == "open"]
        if symbol:
            orders = [o for o in orders if o.symbol.upper() == symbol.upper()]
        return orders

    async def get_user_fills(
        self, symbol: Optional[str] = None, limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Return recent fills."""
        fills = self.fills[-limit:]
        if symbol:
            fills = [f for f in fills if f.get("symbol", "").upper() == symbol.upper()]
        return fills

    # ======================== Order Execution ========================

    async def market_order(
        self,
        symbol: str,
        side: str,
        size: float,
        stop_loss_price: Optional[float] = None,
        take_profit_price: Optional[float] = None,
        **kwargs: Any,
    ) -> OrderResult:
        """Execute a market order instantly at current mid price."""
        symbol = symbol.upper()
        mid = self.mid_prices.get(symbol, 0.0)
        if mid <= 0:
            return OrderResult(
                success=False, error=f"No price available for {symbol}"
            )

        is_long = side.lower() in ("long", "buy")
        slippage = 1.002 if is_long else 0.998  # 20 bps slippage
        fill_price = mid * slippage
        order_id = f"mock-{str(uuid.uuid4())[:8]}"

        # Update position
        if symbol in self.positions:
            pos = self.positions[symbol]
            # This is simplified; real logic would handle side changes, liquidations, etc.
            pos.size += size if is_long else -size
            pos.current_price = fill_price
        else:
            self.positions[symbol] = Position(
                symbol=symbol,
                side="long" if is_long else "short",
                size=size,
                entry_price=fill_price,
                current_price=fill_price,
                leverage=self.leverage.get(symbol, 1),
                unrealized_pnl=0.0,
            )

        # Record fill
        fill = {
            "order_id": order_id,
            "symbol": symbol,
            "side": side,
            "size": size,
            "price": fill_price,
            "timestamp": 0,  # Would use real time in practice
        }
        self.fills.append(fill)

        # Update account value (simplified)
        self.account_value -= (
            size * fill_price
        )  # Would include margin, leverage, etc.

        log.info(
            "MOCK MARKET ORDER: %s %s %.4f @ %.2f", side, symbol, size, fill_price
        )

        return OrderResult(success=True, order_id=order_id, raw_response={"status": "ok"})

    async def limit_order(
        self, symbol: str, side: str, size: float, price: float, **kwargs: Any
    ) -> OrderResult:
        """Create a limit order. In mock, we don't execute it (just hold it)."""
        symbol = symbol.upper()
        order_id = f"mock-limit-{str(uuid.uuid4())[:8]}"

        order = Order(
            order_id=order_id,
            symbol=symbol,
            side=side,
            price=price,
            size=size,
            filled_size=0.0,
            order_type="limit",
            timestamp=0,
            status="open",
        )
        self.orders[order_id] = order

        log.info(
            "MOCK LIMIT ORDER: %s %s %.4f @ %.2f", side, symbol, size, price
        )

        return OrderResult(success=True, order_id=order_id)

    async def cancel_order(
        self, order_id: str, symbol: Optional[str] = None, **kwargs: Any
    ) -> bool:
        """Cancel an order."""
        if order_id in self.orders:
            self.orders[order_id].status = "cancelled"
            log.info("MOCK CANCEL: %s", order_id)
            return True
        return False

    async def close_position(self, symbol: str, **kwargs: Any) -> OrderResult:
        """Close a position with a market order in the opposite direction."""
        symbol = symbol.upper()
        if symbol not in self.positions:
            return OrderResult(
                success=False, error=f"No open position for {symbol}"
            )

        pos = self.positions[symbol]
        close_side = "sell" if pos.side == "long" else "buy"

        result = await self.market_order(
            symbol=symbol, side=close_side, size=abs(pos.size), **kwargs
        )

        if result.success:
            del self.positions[symbol]

        return result

    # ======================== Risk Orders ========================

    async def place_protective_stop(
        self, symbol: str, size: float, trigger_price: float, **kwargs: Any
    ) -> OrderResult:
        """Place a stop-loss trigger order."""
        symbol = symbol.upper()
        order_id = f"mock-stop-{str(uuid.uuid4())[:8]}"

        order = Order(
            order_id=order_id,
            symbol=symbol,
            side="stop",
            price=trigger_price,
            size=size,
            filled_size=0.0,
            order_type="trigger_market",
            timestamp=0,
            status="open",
        )
        self.orders[order_id] = order

        log.info("MOCK STOP-LOSS: %s @ %.2f", symbol, trigger_price)

        return OrderResult(success=True, order_id=order_id)

    async def place_take_profit(
        self, symbol: str, size: float, trigger_price: float, **kwargs: Any
    ) -> OrderResult:
        """Place a take-profit trigger order."""
        symbol = symbol.upper()
        order_id = f"mock-tp-{str(uuid.uuid4())[:8]}"

        order = Order(
            order_id=order_id,
            symbol=symbol,
            side="take_profit",
            price=trigger_price,
            size=size,
            filled_size=0.0,
            order_type="trigger_market",
            timestamp=0,
            status="open",
        )
        self.orders[order_id] = order

        log.info("MOCK TAKE-PROFIT: %s @ %.2f", symbol, trigger_price)

        return OrderResult(success=True, order_id=order_id)

    # ======================== Leverage & Risk ========================

    async def set_leverage(self, symbol: str, leverage: int) -> bool:
        """Set leverage for a symbol."""
        symbol = symbol.upper()
        self.leverage[symbol] = leverage
        log.info("MOCK SET LEVERAGE: %s = %dx", symbol, leverage)
        return True

    # ======================== Market Data ========================

    async def get_all_mids(self) -> Dict[str, float]:
        """Return all current mid prices."""
        return dict(self.mid_prices)

    async def set_mids(self, prices: Dict[str, float]) -> None:
        """Set mock mid prices (helper for testing)."""
        self.mid_prices = {k.upper(): v for k, v in prices.items()}

    async def get_candles(
        self, symbol: str, interval: str = "1h", limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Return mock OHLCV data (all same price for simplicity)."""
        symbol = symbol.upper()
        mid = self.mid_prices.get(symbol, 0.0)

        candles = []
        for i in range(limit):
            candles.append(
                {
                    "timestamp": i,
                    "open": mid,
                    "high": mid * 1.001,
                    "low": mid * 0.999,
                    "close": mid,
                    "volume": 1000.0,
                }
            )

        return candles

    # ======================== Health & Metadata ========================

    async def health_check(self) -> bool:
        """Mock exchange is always healthy."""
        return True

    async def get_exchange_info(self) -> Dict[str, Any]:
        """Return mock exchange metadata."""
        return {
            "type": "mock",
            "symbols": list(self.mid_prices.keys()),
            "testnet": True,
        }
