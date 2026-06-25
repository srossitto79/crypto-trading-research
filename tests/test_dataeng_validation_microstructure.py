from __future__ import annotations

import pandas as pd


def test_build_source_registry_honors_enabled_exchanges():
    from axiom.dataeng.registry import build_source_registry, resolve_source_for_stream
    from axiom.dataeng.settings import DataEngineSettings
    from axiom.dataeng.source import Stream

    settings = DataEngineSettings(
        enabled_exchanges=["binance", "okx"],
        source_priority={"candles": ["okx", "binance"]},
    )
    registry = build_source_registry(settings)

    assert registry.get("binance").id == "binance"
    assert registry.get("okx").id == "okx"
    assert resolve_source_for_stream(registry, settings, Stream.CANDLES).id == "okx"
    try:
        registry.get("bybit")
    except KeyError:
        pass
    else:
        raise AssertionError("disabled exchange should not be registered")


def test_cross_source_validator_flags_and_priority_resolves_divergent_bars():
    from axiom.dataeng.validation import validate_bars

    timestamps = pd.date_range("2026-06-01", periods=2, freq="1h", tz="UTC")
    binance = pd.DataFrame(
        {
            "timestamp": timestamps,
            "open": [100.0, 100.0],
            "high": [101.0, 101.0],
            "low": [99.0, 99.0],
            "close": [100.0, 100.0],
            "volume": [10.0, 10.0],
        }
    )
    okx = binance.copy()
    okx.loc[1, "close"] = 110.0

    result = validate_bars(
        {"binance": binance, "okx": okx},
        policy="flag_priority",
        tolerance_bps=5,
        priority=["binance", "okx"],
    )

    assert result.frame["source"].tolist() == ["binance", "binance"]
    assert result.flags["divergent"].tolist() == [False, True]


def test_derivatives_basis_and_aggregated_oi():
    from axiom.dataeng.derivatives import aggregate_open_interest, perp_spot_basis

    ts = pd.date_range("2026-06-01", periods=2, freq="1h", tz="UTC")
    spot = pd.DataFrame({"timestamp": ts, "close": [100.0, 105.0]})
    perp = pd.DataFrame({"timestamp": ts, "close": [101.0, 103.0]})

    basis = perp_spot_basis(perp, spot)
    assert basis["basis"].tolist() == [1.0, -2.0]
    assert basis["basis_pct"].round(6).tolist() == [0.01, -0.019048]

    oi = aggregate_open_interest(
        {
            "binance": pd.DataFrame({"timestamp": ts, "open_interest": [1.0, 2.0]}),
            "okx": pd.DataFrame({"timestamp": ts, "open_interest": [3.0, 4.0]}),
        }
    )
    assert oi["open_interest"].tolist() == [4.0, 6.0]


def test_microstructure_writer_reader_and_rollup(tmp_path):
    from axiom.dataeng.microstructure import read_micro_rows, rollup_trades_per_minute, write_micro_rows

    trades = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(
                [
                    "2026-06-01T00:00:10Z",
                    "2026-06-01T00:00:40Z",
                    "2026-06-02T00:00:01Z",
                ]
            ),
            "price": [100.0, 101.0, 102.0],
            "amount": [1.0, 0.5, 2.0],
            "side": ["buy", "sell", "buy"],
        }
    )

    written = write_micro_rows("trades", "BTC-USDT", trades, root=tmp_path)
    assert len(written) == 2

    rows = read_micro_rows(
        "trades",
        "BTC-USDT",
        start="2026-06-01T00:00:00Z",
        end="2026-06-01T23:59:59Z",
        root=tmp_path,
    )
    assert len(rows) == 2

    rollup = rollup_trades_per_minute(rows)
    assert rollup["buy_volume"].tolist() == [1.0]
    assert rollup["sell_volume"].tolist() == [0.5]
    assert rollup["cvd"].tolist() == [0.5]
    assert rollup["trade_imbalance"].round(6).tolist() == [0.333333]


def test_onchain_source_disabled_without_key_and_parses_fixture():
    from axiom.dataeng.onchain import OnChainSource
    from axiom.dataeng.source import Stream

    disabled = OnChainSource("coingecko-pro", "")
    assert disabled.health().status == "disabled"
    try:
        disabled.fetch("BTC", Stream.ONCHAIN)
    except Exception as exc:
        assert "disabled" in str(exc)
    else:
        raise AssertionError("disabled on-chain source should fail loudly")

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"prices": [[1_704_067_200_000, 42_000.5]]}

    class Session:
        def get(self, *args, **kwargs):
            return Response()

    source = OnChainSource("coingecko-pro", "secret", session=Session())
    frame = source.fetch("BTC", Stream.ONCHAIN)

    assert frame["timestamp"].tolist() == [pd.Timestamp("2024-01-01T00:00:00Z")]
    assert frame["btc_price_usd"].tolist() == [42_000.5]
