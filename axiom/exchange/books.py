"""Live execution "books" — direction-split sub-account routing (Approach C).

Hyperliquid holds ONE net position per coin per account (no hedge mode), so a
single account cannot be simultaneously long and short the same asset. To let a
long-trend strategy and a short-scalp strategy both hold e.g. BTC, LIVE orders
are routed to one of two funded Hyperliquid sub-accounts ("books") by direction:

  * long book  — holds all net-long exposure
  * short book — holds all net-short exposure

Each book is a funded sub-account with its own net position, margin, and
liquidation. The operator funds and configures the sub-account addresses in
Settings; this module never transfers capital (the wallet is funded manually).

LONG-ONLY mode: until a short-book sub-account is configured (e.g. a fresh
mainnet wallet cannot create a 2nd sub-account until $100k cumulative volume),
short OPEN signals are skipped with a surfaced warning and only longs trade.
Closing an existing short still works (it routes by the trade's stored book).

This is LIVE-only. Paper/simulation trades are local-sim rows and are NEVER
routed here — the scanner only consults this module for live execution_type.

Design notes:
- `book` is a stable label stored on each live trade/position: "long", "short",
  or "main" (the master wallet, used when books are disabled = legacy path).
- `book_address(book)` resolves the label to a sub-account address, or None
  meaning "the master wallet" (used for "main" and for a long book that hasn't
  been pointed at a dedicated sub-account yet).
"""

from __future__ import annotations

import logging

from axiom.db import kv_get

log = logging.getLogger("axiom.exchange.books")

LONG_BOOK = "long"
SHORT_BOOK = "short"
MAIN_BOOK = "main"

# Every label that can be stored on a trade row.
ALL_BOOKS = (LONG_BOOK, SHORT_BOOK, MAIN_BOOK)


def _settings(settings: dict | None = None) -> dict:
    if isinstance(settings, dict):
        return settings
    try:
        raw = kv_get("axiom:settings", {})
    except Exception:
        raw = {}
    return raw if isinstance(raw, dict) else {}


def _clean_address(value: object) -> str | None:
    addr = str(value or "").strip()
    return addr or None


def books_enabled(settings: dict | None = None) -> bool:
    """Master switch. When OFF, everything routes to the main wallet (legacy)."""
    return bool(_settings(settings).get("live_books_enabled", False))


def long_book_address(settings: dict | None = None) -> str | None:
    """Sub-account address for the long book. None => use the master wallet."""
    return _clean_address(_settings(settings).get("hyperliquid_long_book_address"))


def short_book_address(settings: dict | None = None) -> str | None:
    """Sub-account address for the short book. None => short book not provisioned."""
    return _clean_address(_settings(settings).get("hyperliquid_short_book_address"))


def short_book_available(settings: dict | None = None) -> bool:
    """True only when books are enabled AND a short sub-account is configured."""
    s = _settings(settings)
    return books_enabled(s) and short_book_address(s) is not None


def is_long_only(settings: dict | None = None) -> bool:
    """Books enabled but no short sub-account yet => long-only mode."""
    s = _settings(settings)
    return books_enabled(s) and short_book_address(s) is None


def normalize_book(value: object) -> str:
    book = str(value or "").strip().lower()
    return book if book in ALL_BOOKS else MAIN_BOOK


def book_for_direction(direction: str) -> str:
    """Map a trade direction to the book it belongs in (when books are enabled)."""
    return SHORT_BOOK if str(direction or "").strip().lower() == "short" else LONG_BOOK


def opposite_book(book: str) -> str | None:
    """The other direction book (M7 self-trade guard). MAIN/unknown -> None."""
    label = normalize_book(book)
    if label == LONG_BOOK:
        return SHORT_BOOK
    if label == SHORT_BOOK:
        return LONG_BOOK
    return None


def book_address(book: str, settings: dict | None = None) -> str | None:
    """Resolve a book label to its sub-account address.

    Returns None for the master wallet ("main", or a long book with no dedicated
    sub-account configured). Raises nothing — callers treat None as "master".
    """
    s = _settings(settings)
    label = normalize_book(book)
    if label == LONG_BOOK:
        return long_book_address(s)
    if label == SHORT_BOOK:
        return short_book_address(s)
    return None


def resolve_open_book(direction: str, settings: dict | None = None) -> tuple[str | None, str | None]:
    """Decide which book a NEW live position opens into.

    Returns (book_label, skip_reason):
      * (book, None)  -> open into this book.
      * (None, reason) -> skip the open; `reason` is an operator-facing warning
        (used for long-only mode when a short can't be placed).

    When books are disabled this always returns ("main", None) so live behaves
    exactly as today (single shared wallet).
    """
    s = _settings(settings)
    if not books_enabled(s):
        return MAIN_BOOK, None

    direction_l = str(direction or "").strip().lower()
    if direction_l == "short":
        if short_book_address(s) is None:
            return None, (
                "LONG ONLY: short-book sub-account is not configured — short signal "
                "skipped. Add a Hyperliquid short sub-account in Settings (you may "
                "need $100k cumulative volume to create a 2nd sub-account) to enable "
                "shorts."
            )
        return SHORT_BOOK, None
    return LONG_BOOK, None


def active_book_addresses(settings: dict | None = None) -> list[tuple[str, str | None]]:
    """The (book_label, address) pairs the reconciler must snapshot independently.

    - Books disabled: just the master wallet [("main", None)].
    - Books enabled: the long book and (if configured) the short book. The long
      book may itself be the master wallet (address None) when no dedicated long
      sub-account is set.
    """
    s = _settings(settings)
    if not books_enabled(s):
        return [(MAIN_BOOK, None)]
    pairs: list[tuple[str | None, str | None]] = [(LONG_BOOK, long_book_address(s))]
    short_addr = short_book_address(s)
    if short_addr is not None:
        pairs.append((SHORT_BOOK, short_addr))
    # Deduplicate by resolved address so a long book pointed at the master wallet
    # (None) isn't snapshotted twice if main also appears.
    seen: set[str | None] = set()
    result: list[tuple[str, str | None]] = []
    for label, addr in pairs:
        if addr in seen:
            continue
        seen.add(addr)
        result.append((label, addr))
    return result


# Hyperliquid requires this much cumulative trading volume before a (mainnet)
# master wallet can create additional sub-accounts. Surfaced so the operator
# understands WHY they may be stuck in long-only mode.
SUBACCOUNT_VOLUME_REQUIREMENT_USD = 100_000


def live_books_status(settings: dict | None = None) -> dict:
    """Compact status for the API/UI risk display."""
    s = _settings(settings)
    enabled = books_enabled(s)
    long_only = is_long_only(s)
    note = None
    if long_only:
        note = (
            "LONG ONLY: no short sub-account configured — short signals are skipped. "
            "Hyperliquid requires ~$100k cumulative trading volume before a mainnet "
            "master wallet can create a 2nd (short) sub-account; once available, set it "
            "in Settings → Risk → Short-book sub-account address."
        )
    return {
        "enabled": enabled,
        "long_only": long_only,
        "long_book_configured": long_book_address(s) is not None,
        "short_book_configured": short_book_address(s) is not None,
        "subaccount_volume_requirement_usd": SUBACCOUNT_VOLUME_REQUIREMENT_USD,
        "note": note,
    }
