"""Verify Hyperliquid sub-account (direction-book) routing on TESTNET.

Approach C routes live orders to a sub-account via the SDK `vault_address` (the
master key signs; the order executes on behalf of the sub-account). Whether the
configured master/agent key is actually allowed to sign for a given sub-account
is the one thing that can't be unit-tested — it depends on Hyperliquid's
agent-approval model. This script proves it against your real testnet sub-account.

USAGE (run it yourself so it uses YOUR configured credentials):

    # 1) Reads only (safe): prove routed balance/position reads work.
    .venv/Scripts/python scripts/verify_subaccount_routing.py 0xYourSubAccountAddr

    # 2) Also prove SIGNING: place a tiny far-from-market limit order on the
    #    sub-account and immediately cancel it (won't fill; opt-in).
    .venv/Scripts/python scripts/verify_subaccount_routing.py 0xYourSubAccountAddr --place-test-order

If no address is passed it falls back to hyperliquid_short_book_address, then
hyperliquid_long_book_address from settings. TESTNET ONLY by default.
"""

from __future__ import annotations

import argparse
import sys


def _resolve_subaccount(explicit: str | None) -> str | None:
    if explicit:
        return explicit.strip()
    try:
        from axiom.db import kv_get
        s = kv_get("axiom:settings", {}) or {}
        for key in ("hyperliquid_short_book_address", "hyperliquid_long_book_address"):
            addr = str(s.get(key) or "").strip()
            if addr:
                return addr
    except Exception:
        pass
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify Hyperliquid sub-account routing on testnet")
    parser.add_argument("subaccount", nargs="?", help="Sub-account address (defaults to short/long book setting)")
    parser.add_argument("--place-test-order", action="store_true", help="Also place+cancel a tiny resting order to prove signing")
    parser.add_argument("--asset", default="BTC", help="Asset for the test order (default BTC)")
    parser.add_argument("--mainnet", action="store_true", help="Run against mainnet instead of testnet (NOT recommended)")
    args = parser.parse_args()

    testnet = not args.mainnet
    sub = _resolve_subaccount(args.subaccount)
    if not sub:
        print("FAIL: no sub-account address given and none configured in settings.")
        return 2

    from axiom.exchange.hyperliquid import (
        cancel_order,
        get_account_value,
        get_all_mids,
        get_positions,
        limit_order,
        round_to_tick,
    )

    net = "testnet" if testnet else "MAINNET"
    print(f"== Verifying sub-account routing on {net} ==")
    print(f"Sub-account: {sub}\n")

    # 1) Routed reads — proves the sub-account address is reachable and distinct.
    try:
        master_val = get_account_value(testnet=testnet)
        sub_val = get_account_value(testnet=testnet, account_address=sub)
        print(f"[reads] master accountValue = ${float(master_val.get('accountValue', 0)):,.2f}")
        print(f"[reads] sub    accountValue = ${float(sub_val.get('accountValue', 0)):,.2f}")
        sub_pos = get_positions(testnet=testnet, account_address=sub)
        n_pos = len(sub_pos.get("positions", []) if isinstance(sub_pos, dict) else [])
        print(f"[reads] sub open positions  = {n_pos}")
        print("[reads] OK — routed reads against the sub-account succeeded.\n")
    except Exception as exc:
        print(f"[reads] FAIL — could not read the sub-account: {exc}")
        return 1

    if not args.place_test_order:
        print("Reads verified. Re-run with --place-test-order to prove order SIGNING for the sub-account.")
        return 0

    # 2) Signing test — place a tiny resting buy far below market (won't fill),
    #    then cancel it. Success here is the definitive proof that the master
    #    key can sign orders routed to this sub-account.
    asset = args.asset.upper()
    try:
        mids = get_all_mids(testnet=testnet)
        mid = float(mids.get(asset, 0) or 0)
    except Exception as exc:
        print(f"[order] FAIL — could not fetch mid for {asset}: {exc}")
        return 1
    if mid <= 0:
        print(f"[order] FAIL — no mid price for {asset}.")
        return 1

    # 4% below mid: a resting BUY that won't fill, yet stays inside the client's
    # 5%-from-mid stale-order guard so it actually reaches the exchange.
    limit_px = round_to_tick(mid * 0.96, asset)
    # ~$15 notional (clears the $10 minimum) rounded to 5 decimals — a valid BTC
    # lot size (szDecimals=5). Other assets may use different size precision; if
    # you change --asset and hit "Order has invalid size", adjust the rounding.
    size = round(max(15.0 / limit_px, 0.0), 5)
    print(f"[order] placing tiny resting BUY {size} {asset} @ {limit_px} (mid={mid}) on the sub-account...")
    res = limit_order(asset, "buy", size, limit_px, testnet=testnet, vault_address=sub, tif="Gtc")
    if isinstance(res, dict) and res.get("error"):
        print(f"[order] FAIL — order rejected: {res.get('error')}")
        print("        If this is an AUTHORIZATION/agent error, the master key is NOT approved to")
        print("        sign for this sub-account — approve it from the master wallet first.")
        return 1

    order_ids = res.get("order_ids") if isinstance(res, dict) else None
    oid = (order_ids or {}).get("entry") or res.get("order_id") or res.get("entry_order_id")
    print(f"[order] OK — order accepted (oid={oid}). Signing for the sub-account WORKS.")

    if oid is None:
        print("[order] WARN — no order id returned; cancel manually if it rested.")
        return 0
    try:
        cancel_res = cancel_order(asset, int(oid), testnet=testnet, vault_address=sub)
        print(f"[order] cancelled test order: {cancel_res}")
    except Exception as exc:
        print(f"[order] WARN — could not cancel test order {oid}: {exc} (cancel it manually).")

    print("\nPASS: sub-account routing + signing verified.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
