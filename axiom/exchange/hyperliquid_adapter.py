"""HyperliquidExchange adapter - wraps hyperliquid.py functions with ExchangeInterface."""

import asyncio
import logging
from typing import Any, Dict, List, Optional

from axiom.exchange.interface import (
    ExchangeInterface,
    Order,
    OrderResult,
    Position,
)

log = logging.getLogger("axiom.exchange.hyperliquid_adapter")


class HyperliquidExchange(ExchangeInterface):
    """
    Wraps Hyperliquid SDK calls through ExchangeInterface.

    This is a thin adapter around the synchronous hyperliquid.py functions.
    It provides async-friendly interface and standardized return types.
    """

    def __init__(self, testnet: bool = True):
        self.testnet = testnet

    # ======================== Account & Positions ========================

    async def get_account_value(self) -> float:
        """Get total account value in USD."""
        # Import here to avoid circular dependency
        from axiom.exchange.hyperliquid import get_account_value as hl_get_account_value

        try:
            result = await asyncio.to_thread(
                hl_get_account_value, testnet=self.testnet
            )
            if isinstance(result, dict):
                return float(result.get("accountValue", 0.0))
            return float(result)
        except Exception as e:
            log.error("Failed to get account value: %s", e)
            return 0.0

    async def get_positions(self) -> List[Position]:
        """Get all open positions."""
        from axiom.exchange.hyperliquid import get_positions as hl_get_positions

        try:
            result = await asyncio.to_thread(
                hl_get_positions, testnet=self.testnet
            )
            if isinstance(result, dict):
                positions_data = result.get("positions", [])
            else:
                positions_data = result

            positions = []
            for p in positions_data:
                if isinstance(p, dict) and "position" in p:
                    pos_data = p["position"]
                    positions.append(
                        Position(
                            symbol=pos_data.get("coin", "").upper(),
                            side="long"
                            if float(pos_data.get("szi", 0)) > 0
                            else "short",
                            size=abs(float(pos_data.get("szi", 0))),
                            entry_price=float(pos_data.get("entryPx", 0)),
                            current_price=float(pos_data.get("entryPx", 0)),
                            leverage=pos_data.get("leverage", {}).get("value", 1),
                            unrealized_pnl=0.0,
                        )
                    )
            return positions
        except Exception as e:
            log.error("Failed to get positions: %s", e)
            return []

    async def get_open_orders(self, symbol: Optional[str] = None) -> List[Order]:
        """Get all open orders."""
        from axiom.exchange.hyperliquid import (
            get_open_orders as hl_get_open_orders,
        )

        try:
            result = await asyncio.to_thread(
                hl_get_open_orders, testnet=self.testnet
            )
            orders = []
            for o in result:
                if isinstance(o, dict):
                    order = Order(
                        order_id=o.get("orderId", ""),
                        symbol=o.get("coin", "").upper(),
                        side=o.get("side", ""),
                        price=float(o.get("limitPx", 0)),
                        size=float(o.get("sz", 0)),
                        filled_size=float(o.get("filledSz", 0)),
                        order_type=o.get("orderType", ""),
                        timestamp=float(o.get("timestamp", 0)),
                        status="open",
                    )
                    if symbol is None or order.symbol.upper() == symbol.upper():
                        orders.append(order)
            return orders
        except Exception as e:
            log.error("Failed to get open orders: %s", e)
            return []

    async def get_user_fills(
        self, symbol: Optional[str] = None, limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Get recent fills."""
        from axiom.exchange.hyperliquid import (
            get_user_fills as hl_get_user_fills,
        )

        try:
            result = await asyncio.to_thread(
                hl_get_user_fills, symbol=symbol, testnet=self.testnet
            )
            return result if isinstance(result, list) else []
        except Exception as e:
            log.error("Failed to get user fills: %s", e)
            return []

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
        """Execute a market order."""
        from axiom.exchange.hyperliquid import (
            market_order as hl_market_order,
        )

        try:
            result = await asyncio.to_thread(
                hl_market_order,
                asset=symbol,
                side=side,
                size=size,
                stop_loss_price=stop_loss_price,
                take_profit_price=take_profit_price,
                testnet=self.testnet,
            )

            success = result.get("status") == "ok" or "error" not in result
            order_id = None
            if success:
                order_id = result.get("order_id") or result.get("entry_order_id")

            return OrderResult(
                success=success,
                order_id=order_id,
                error=result.get("error"),
                raw_response=result,
            )
        except Exception as e:
            return OrderResult(success=False, error=str(e))

    async def limit_order(
        self, symbol: str, side: str, size: float, price: float, **kwargs: Any
    ) -> OrderResult:
        """Execute a limit order."""
        from axiom.exchange.hyperliquid import (
            limit_order as hl_limit_order,
        )

        try:
            result = await asyncio.to_thread(
                hl_limit_order,
                asset=symbol,
                side=side,
                size=size,
                limit_px=price,
                testnet=self.testnet,
            )

            success = result.get("status") == "ok" or "error" not in result
            order_id = None
            if success:
                order_id = result.get("order_id")

            return OrderResult(
                success=success,
                order_id=order_id,
                error=result.get("error"),
                raw_response=result,
            )
        except Exception as e:
            return OrderResult(success=False, error=str(e))

    async def cancel_order(
        self, order_id: str, symbol: Optional[str] = None, **kwargs: Any
    ) -> bool:
        """Cancel an order."""
        from axiom.exchange.hyperliquid import (
            cancel_order as hl_cancel_order,
        )

        try:
            result = await asyncio.to_thread(
                hl_cancel_order,
                asset=symbol or "",
                oid=int(order_id),
                testnet=self.testnet,
            )
            return result.get("status") == "ok"
        except Exception as e:
            log.error("Failed to cancel order: %s", e)
            return False

    async def close_position(self, symbol: str, **kwargs: Any) -> OrderResult:
        """Close a position."""
        from axiom.exchange.hyperliquid import (
            close_position as hl_close_position,
        )

        try:
            result = await asyncio.to_thread(
                hl_close_position, asset=symbol, testnet=self.testnet
            )

            success = result.get("status") == "ok" or "error" not in result
            order_id = None
            if success:
                order_id = result.get("order_id")

            return OrderResult(
                success=success,
                order_id=order_id,
                error=result.get("error"),
                raw_response=result,
            )
        except Exception as e:
            return OrderResult(success=False, error=str(e))

    # ======================== Risk Orders ========================

    async def place_protective_stop(
        self, symbol: str, size: float, trigger_price: float, **kwargs: Any
    ) -> OrderResult:
        """Place a stop-loss order."""
        from axiom.exchange.hyperliquid import (
            place_protective_stop as hl_place_protective_stop,
        )

        try:
            result = await asyncio.to_thread(
                hl_place_protective_stop,
                asset=symbol,
                size=size,
                trigger_px=trigger_price,
                testnet=self.testnet,
            )

            success = result.get("status") == "ok" or "error" not in result
            order_id = None
            if success:
                order_id = result.get("order_id")

            return OrderResult(
                success=success,
                order_id=order_id,
                error=result.get("error"),
                raw_response=result,
            )
        except Exception as e:
            return OrderResult(success=False, error=str(e))

    async def place_take_profit(
        self, symbol: str, size: float, trigger_price: float, **kwargs: Any
    ) -> OrderResult:
        """Place a take-profit order."""
        from axiom.exchange.hyperliquid import (
            place_take_profit as hl_place_take_profit,
        )

        try:
            result = await asyncio.to_thread(
                hl_place_take_profit,
                asset=symbol,
                size=size,
                trigger_px=trigger_price,
                testnet=self.testnet,
            )

            success = result.get("status") == "ok" or "error" not in result
            order_id = None
            if success:
                order_id = result.get("order_id")

            return OrderResult(
                success=success,
                order_id=order_id,
                error=result.get("error"),
                raw_response=result,
            )
        except Exception as e:
            return OrderResult(success=False, error=str(e))

    # ======================== Leverage & Risk ========================

    async def set_leverage(self, symbol: str, leverage: int) -> bool:
        """Set leverage for a symbol."""
        from axiom.exchange.hyperliquid import (
            set_leverage as hl_set_leverage,
        )

        try:
            result = await asyncio.to_thread(
                hl_set_leverage,
                asset=symbol,
                leverage=leverage,
                testnet=self.testnet,
            )
            return result.get("status") == "ok"
        except Exception as e:
            log.error("Failed to set leverage: %s", e)
            return False

    # ======================== Market Data ========================

    async def get_all_mids(self) -> Dict[str, float]:
        """Fetch current mid prices for all symbols."""
        from axiom.exchange.hyperliquid import (
            get_all_mids as hl_get_all_mids,
        )

        try:
            result = await asyncio.to_thread(hl_get_all_mids, testnet=self.testnet)
            return result if isinstance(result, dict) else {}
        except Exception as e:
            log.error("Failed to get all mids: %s", e)
            return {}

    async def get_candles(
        self, symbol: str, interval: str = "1h", limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Fetch OHLCV candles."""
        from axiom.exchange.hyperliquid import (
            get_candles as hl_get_candles,
        )

        try:
            result = await asyncio.to_thread(
                hl_get_candles,
                coin=symbol,
                interval=interval,
                bars=limit,
                testnet=self.testnet,
            )
            return result if isinstance(result, list) else []
        except Exception as e:
            log.error("Failed to get candles: %s", e)
            return []

    # ======================== Health & Metadata ========================

    async def health_check(self) -> bool:
        """Check if Hyperliquid is reachable."""
        try:
            mids = await self.get_all_mids()
            return len(mids) > 0
        except Exception:
            return False

    async def get_exchange_info(self) -> Dict[str, Any]:
        """Get exchange metadata."""
        return {
            "name": "hyperliquid",
            "testnet": self.testnet,
            "type": "perpetuals",
        }
