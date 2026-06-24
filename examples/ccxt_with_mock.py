#!/usr/bin/env python
"""
Example: Use CCXT for live price feeds with MockExchange for paper trading.

This combines the best of both worlds:
- Real market prices from CCXT
- Safe paper trading with MockExchange
- No real capital needed
"""

import asyncio
from forven.exchange import CCXTExchange, MockExchange
from forven.exchange.hyperliquid import set_exchange, get_exchange


async def main():
    """Example: Paper trading with live Binance prices."""

    # Step 1: Create CCXT exchange for price feeds (no auth needed for public data)
    print("[1] Creating CCXT Binance connection (for price feeds)...")
    binance = CCXTExchange(
        exchange_id='binance',
        api_key='',  # Not needed for public data
        api_secret='',
    )

    # Step 2: Create MockExchange for safe order execution
    print("[2] Creating MockExchange (for safe order execution)...")
    mock = MockExchange()

    # Step 3: Fetch live prices from CCXT
    print("[3] Fetching live prices from Binance...")
    mids = await binance.get_all_mids()
    print(f"   BTC: ${mids.get('BTC', 'N/A')}")
    print(f"   ETH: ${mids.get('ETH', 'N/A')}")

    # Step 4: Set mock prices to match live prices
    print("[4] Syncing mock exchange prices...")
    mock.set_mids(mids)

    # Step 5: Use mock for order execution
    print("[5] Setting active exchange to MockExchange...")
    set_exchange(mock)

    # Step 6: Execute trades (paper trading)
    print("[6] Executing paper trades...")
    exchange = get_exchange()

    # Check account
    account_value = await exchange.get_account_value()
    print(f"   Account value: ${account_value:,.2f}")

    # Place a buy order
    print("[7] Placing market buy order (BTC/USDT, 0.01)...")
    result = await exchange.market_order('BTC', 'buy', 0.01)
    print(f"   Success: {result.success}")
    print(f"   Order ID: {result.order_id}")

    # Check positions
    print("[8] Checking positions...")
    positions = await exchange.get_positions()
    for pos in positions:
        print(f"   {pos.symbol}: {pos.size} @ ${pos.entry_price}")

    # Get recent fills
    print("[9] Checking recent fills...")
    fills = await exchange.get_user_fills(limit=5)
    for fill in fills:
        print(f"   {fill['side'].upper()} {fill['amount']} {fill['symbol']} @ ${fill['price']}")

    # Close position
    print("[10] Closing position...")
    result = await exchange.close_position('BTC')
    print(f"    Success: {result.success}")

    # Check final account
    account_value = await exchange.get_account_value()
    print(f"[11] Final account value: ${account_value:,.2f}")


async def example_live_trading():
    """Example: Live trading with real CCXT exchange."""

    print("\n=== Live Trading Example (Requires API Keys) ===\n")

    # Note: Replace with your actual API keys
    API_KEY = "your_api_key_here"
    API_SECRET = "your_api_secret_here"

    if API_KEY == "your_api_key_here":
        print("Skipping live example (API keys not configured)")
        return

    print("[1] Creating CCXT Binance with credentials...")
    binance = CCXTExchange(
        exchange_id='binance',
        api_key=API_KEY,
        api_secret=API_SECRET,
        testnet=True,  # Use testnet for safety!
    )

    print("[2] Setting as active exchange...")
    set_exchange(binance)

    print("[3] Fetching account...")
    exchange = get_exchange()
    account_value = await exchange.get_account_value()
    print(f"   Account value: ${account_value:,.2f}")

    print("[4] Fetching open orders...")
    orders = await exchange.get_open_orders()
    print(f"   Found {len(orders)} open orders")

    print("[5] Fetching positions...")
    positions = await exchange.get_positions()
    print(f"   Found {len(positions)} open positions")

    for pos in positions:
        print(f"   - {pos.symbol}: {pos.size} @ ${pos.entry_price}")


async def example_switch_exchanges():
    """Example: Switch between exchanges at runtime."""

    print("\n=== Exchange Switching Example ===\n")

    print("[1] Creating multiple exchanges...")
    binance = CCXTExchange(exchange_id='binance', api_key='', api_secret='')
    kraken = CCXTExchange(exchange_id='kraken', api_key='', api_secret='')
    mock = MockExchange()

    print("[2] Setting to Binance...")
    set_exchange(binance)
    exchange = get_exchange()
    info = await exchange.get_exchange_info()
    print(f"   Active: {info['name']}")

    print("[3] Switching to Kraken...")
    set_exchange(kraken)
    exchange = get_exchange()
    info = await exchange.get_exchange_info()
    print(f"   Active: {info['name']}")

    print("[4] Switching to MockExchange...")
    set_exchange(mock)
    exchange = get_exchange()
    info = await exchange.get_exchange_info()
    print(f"   Active: {info['name']}")

    print("\n✅ Successfully switched between 3 different exchanges!")


async def example_get_candles():
    """Example: Fetch OHLCV candles from CCXT."""

    print("\n=== Candle Data Example ===\n")

    print("[1] Creating CCXT Binance...")
    binance = CCXTExchange(exchange_id='binance', api_key='', api_secret='')

    print("[2] Fetching BTC/USDT 1h candles (last 24 hours)...")
    candles = await binance.get_candles('BTC/USDT', interval='1h', limit=24)

    print("\n   Time        | Open    | Close   | High    | Low")
    print("   " + "-" * 50)
    for candle in candles[-5:]:  # Show last 5
        timestamp = candle['timestamp'] // 1000
        print(
            f"   {timestamp} | "
            f"${candle['open']:7.0f} | "
            f"${candle['close']:7.0f} | "
            f"${candle['high']:7.0f} | "
            f"${candle['low']:7.0f}"
        )

    print(f"\n✅ Fetched {len(candles)} candles")


if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("Forven CCXT Integration Examples")
    print("=" * 60 + "\n")

    # Run paper trading example (safe, no auth needed)
    print("=== Paper Trading Example (Recommended) ===\n")
    asyncio.run(main())

    # Uncomment to run other examples:
    # asyncio.run(example_live_trading())
    # asyncio.run(example_switch_exchanges())
    # asyncio.run(example_get_candles())

    print("\n" + "=" * 60)
    print("✅ All examples completed!")
    print("=" * 60)
