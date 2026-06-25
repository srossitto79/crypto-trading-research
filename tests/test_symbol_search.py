"""Symbol typeahead search: source/exchange routing for the Data Manager fetch form.

The fetch form's Symbol field is a live typeahead backed by
search_source_symbols -> search_ccxt_symbols(exchange). These tests pin the
routing rules (which exchange's markets get searched) without hitting the
network, so the "ccxt honours the picked exchange / binance forces binance /
unknown falls back" contract can't silently regress.
"""
from __future__ import annotations


def test_ccxt_search_honours_selected_exchange(monkeypatch):
    from axiom import data as d

    captured: dict[str, object] = {}
    monkeypatch.setattr(
        d, "search_ccxt_symbols",
        lambda q, exchange_id="binance", limit=200: captured.update(q=q, ex=exchange_id, limit=limit) or [],
    )

    d.search_source_symbols("ccxt", query="btc", exchange="bybit")
    assert captured["ex"] == "bybit"
    assert captured["q"] == "btc"


def test_binance_source_always_searches_binance(monkeypatch):
    from axiom import data as d

    captured: dict[str, object] = {}
    monkeypatch.setattr(
        d, "search_ccxt_symbols",
        lambda q, exchange_id="binance", limit=200: captured.update(ex=exchange_id) or [],
    )

    # Binance Direct ignores any exchange hint and always queries binance.
    d.search_source_symbols("binance", query="eth", exchange="kraken")
    assert captured["ex"] == "binance"


def test_unknown_exchange_falls_back_to_binance(monkeypatch):
    from axiom import data as d

    captured: dict[str, object] = {}
    monkeypatch.setattr(
        d, "search_ccxt_symbols",
        lambda q, exchange_id="binance", limit=200: captured.update(ex=exchange_id) or [],
    )

    # An id outside the allow-list must not trigger an arbitrary market load.
    d.search_source_symbols("ccxt", query="sol", exchange="not-a-real-exchange")
    assert captured["ex"] == "binance"


def test_empty_query_short_circuits_without_loading_markets(monkeypatch):
    from axiom import data as d

    called = {"n": 0}
    monkeypatch.setattr(
        d, "search_ccxt_symbols",
        lambda *a, **k: called.update(n=called["n"] + 1) or [],
    )

    assert d.search_source_symbols("ccxt", query="", exchange="bybit") == []
    assert d.search_source_symbols("ccxt", query="   ") == []
    assert called["n"] == 0  # never reached the market-loading layer


def test_unsupported_source_returns_empty(monkeypatch):
    from axiom import data as d

    called = {"n": 0}
    monkeypatch.setattr(
        d, "search_ccxt_symbols",
        lambda *a, **k: called.update(n=called["n"] + 1) or [],
    )

    assert d.search_source_symbols("polygon", query="aapl") == []
    assert d.search_source_symbols("yahoo", query="aapl") == []
    assert called["n"] == 0


def test_search_ccxt_symbols_excludes_non_spot_markets(monkeypatch):
    """Perps/futures must not be offered: the fetch pipeline is spot-only, so a
    perp pick (e.g. BTC/USDT:USDT) would silently collapse to and fetch the SPOT
    series. Only type == 'spot' markets may surface in the typeahead."""
    from axiom import data as d

    fake_markets = {
        "BTC/USDT": {"symbol": "BTC/USDT", "base": "BTC", "quote": "USDT", "type": "spot", "active": True},
        "BTC/USDT:USDT": {"symbol": "BTC/USDT:USDT", "base": "BTC", "quote": "USDT", "type": "swap", "active": True},
        "ETH/USDT": {"symbol": "ETH/USDT", "base": "ETH", "quote": "USDT", "type": "spot", "active": True},
        "ETH/USDT-250101": {"symbol": "ETH/USDT-250101", "base": "ETH", "quote": "USDT", "type": "future", "active": True},
    }
    monkeypatch.setattr(d, "_cached_markets", lambda exchange_id: dict(fake_markets))

    out = d.search_ccxt_symbols("usdt", exchange_id="binance")
    assert {row["name"] for row in out} == {"BTC/USDT", "ETH/USDT"}  # spot only
    assert all((row.get("type") or "spot") == "spot" for row in out)


def test_exchange_casing_and_whitespace_normalized(monkeypatch):
    from axiom import data as d

    captured: dict[str, object] = {}
    monkeypatch.setattr(
        d, "search_ccxt_symbols",
        lambda q, exchange_id="binance", limit=200: captured.update(ex=exchange_id) or [],
    )

    d.search_source_symbols("ccxt", query="btc", exchange="  BYBIT  ")
    assert captured["ex"] == "bybit"  # stripped + lowercased, still inside the allow-list


def test_search_source_symbols_stub_threads_exchange(monkeypatch):
    """The no-source-prefix entry point must also carry exchange through."""
    from axiom import data as d
    from axiom.api_domains import data as dd

    captured: dict[str, object] = {}
    monkeypatch.setattr(
        d, "search_source_symbols",
        lambda source, query=None, limit=200, exchange=None: captured.update(
            source=source, exchange=exchange
        ) or [],
    )

    dd.search_source_symbols_stub(source="ccxt", query="eth", exchange="okx")
    assert captured == {"source": "ccxt", "exchange": "okx"}


def test_domain_layer_threads_exchange(monkeypatch):
    """The router -> domain -> data hop must carry the exchange param through."""
    from axiom import data as d
    from axiom.api_domains import data as dd

    captured: dict[str, object] = {}
    monkeypatch.setattr(
        d, "search_source_symbols",
        lambda source, query=None, limit=200, exchange=None: captured.update(
            source=source, query=query, exchange=exchange
        ) or [{"symbol": "BTC/USDT"}],
    )

    out = dd.get_source_symbols_stub("ccxt", query="btc", exchange="okx")
    assert captured == {"source": "ccxt", "query": "btc", "exchange": "okx"}
    assert out and out[0]["symbol"] == "BTC/USDT"
