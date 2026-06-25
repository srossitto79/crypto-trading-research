from __future__ import annotations

import pandas as pd


class _FakeSource:
    id = "fake"

    def __init__(self, capabilities):
        self.capabilities = set(capabilities)

    def fetch(self, ref, stream, since=None, until=None):
        return pd.DataFrame()

    async def stream(self, ref, stream):
        yield pd.DataFrame()

    def health(self):
        from axiom.dataeng.source import SourceHealth

        return SourceHealth(source=self.id, status="closed")


def test_source_registry_register_lookup_and_capability():
    from axiom.dataeng.source import SourceRegistry, Stream

    registry = SourceRegistry()
    source = _FakeSource({Stream.CANDLES})
    registry.register(source)

    assert registry.get("fake") is source
    assert registry.supports("fake", Stream.CANDLES) is True
    assert registry.supports("fake", Stream.FUNDING) is False
    assert registry.resolve(Stream.CANDLES, ["fake"]) is source


def test_source_registry_breaker_trips_and_recovers():
    from axiom.dataeng.source import SourceRegistry, Stream

    registry = SourceRegistry()
    registry.register(_FakeSource({Stream.CANDLES}))

    registry.record_failure("fake", "one")
    assert registry.health("fake").status == "degraded"
    registry.record_failure("fake", "two")
    registry.record_failure("fake", "three")
    assert registry.health("fake").status == "open"

    try:
        registry.resolve(Stream.CANDLES, ["fake"])
    except KeyError:
        pass
    else:
        raise AssertionError("open breaker should not resolve")

    registry.record_success("fake")
    health = registry.health("fake")
    assert health.status == "closed"
    assert health.consecutive_failures == 0


def test_ccxt_source_fetches_candles_funding_and_oi_from_exchange_fixture():
    from axiom.dataeng.ccxt_source import CcxtSource
    from axiom.dataeng.identity import to_ref
    from axiom.dataeng.source import Stream

    class FakeExchange:
        def fetch_ohlcv(self, symbol, timeframe, since, limit):
            assert symbol == "BTC/USDT"
            assert timeframe == "1h"
            assert since == 1_704_067_200_000
            assert limit == 1000
            return [[1_704_067_200_000, 1, 2, 0.5, 1.5, 10]]

        def fetch_funding_rate_history(self, symbol, since, limit):
            assert symbol == "BTC/USDT:USDT"
            assert since is None
            assert limit == 1000
            return [{"timestamp": 1_704_067_200_000, "fundingRate": "0.0001"}]

        def fetch_open_interest_history(self, symbol, timeframe, since, limit):
            assert symbol == "BTC/USDT:USDT"
            assert timeframe == "1h"
            assert since is None
            assert limit == 500
            return [{"timestamp": 1_704_067_200_000, "openInterestAmount": "123.4"}]

    source = CcxtSource("binance", exchange=FakeExchange())
    ref = to_ref("BTC-USDT", timeframe="1h")

    candles = source.fetch(ref, Stream.CANDLES, since=1_704_067_200_000)
    funding = source.fetch(ref, Stream.FUNDING)
    oi = source.fetch(ref, Stream.OI)

    assert list(candles.columns) == ["timestamp", "open", "high", "low", "close", "volume"]
    assert candles["close"].tolist() == [1.5]
    assert funding["funding_rate"].tolist() == [0.0001]
    assert oi["open_interest"].tolist() == [123.4]


def test_funding_and_oi_collectors_use_source_registry_when_enabled(AXIOM_db, tmp_path, monkeypatch):
    from axiom import api_core
    from axiom.data_manager import FundingCollector, OICollector

    api_core.put_settings_section("data-engine", {"enabled": True})
    monkeypatch.setattr("axiom.data_manager.FUNDING_DIR", tmp_path / "funding")
    monkeypatch.setattr("axiom.data_manager.OI_DIR", tmp_path / "oi")

    calls: list[tuple[str, str, str | None]] = []

    def fake_fetch(symbol, stream_name, *, timeframe=None, since=None):
        calls.append((symbol, stream_name, timeframe))
        if stream_name == "funding":
            return pd.DataFrame(
                {
                    "timestamp": pd.to_datetime(["2026-06-01T00:00:00Z"]),
                    "funding_rate": [0.001],
                }
            )
        return pd.DataFrame(
            {
                "timestamp": pd.to_datetime(["2026-06-01T00:00:00Z"]),
                "open_interest": [1000.0],
            }
        )

    monkeypatch.setattr("axiom.data_manager._fetch_stream_via_source_registry", fake_fetch)
    monkeypatch.setattr(
        "axiom.data_manager._get_futures_exchange",
        lambda: (_ for _ in ()).throw(AssertionError("legacy exchange path should not run")),
    )

    assert FundingCollector().collect("BTC-USDT") == 1
    assert OICollector().collect("BTC-USDT", "1h") == 1
    assert calls == [("BTC-USDT", "funding", None), ("BTC-USDT", "oi", "1h")]
