from axiom import auto_trim


def test_extract_referenced_columns_ignores_strategy_type_and_metadata(monkeypatch):
    monkeypatch.setattr(
        auto_trim,
        "_metric_universe",
        lambda: frozenset({"funding_rate", "open_interest"}),
    )
    code = '''
TYPE_NAME = "funding_rate_scalper"

class FundingRateScalper:
    def generate_signal(self, df):
        return 0 if df["close"].iloc[-1] else 0
'''
    params = {
        "strategy_name": "open_interest edge",
        "name": "funding_rate experiment",
    }

    assert auto_trim._extract_referenced_columns("funding_rate_scalper", params, code) == set()


def test_extract_referenced_columns_reads_dataframe_access(monkeypatch):
    monkeypatch.setattr(
        auto_trim,
        "_metric_universe",
        lambda: frozenset({"funding_rate", "open_interest", "liq_total_volume"}),
    )
    code = '''
class MacroEdge:
    def generate_signal(self, df):
        cols = ["liq_total_volume"]
        if "funding_rate" in df.columns:
            return df["funding_rate"].iloc[-1] > df.get("open_interest").iloc[-1]
        return df[cols[0]].iloc[-1] > 0
'''

    assert auto_trim._extract_referenced_columns(strategy_code=code) == {
        "funding_rate",
        "open_interest",
        "liq_total_volume",
    }


def test_extract_referenced_columns_reads_non_metadata_params(monkeypatch):
    monkeypatch.setattr(
        auto_trim,
        "_metric_universe",
        lambda: frozenset({"funding_rate", "open_interest"}),
    )
    params = {
        "strategy_name": "open_interest edge",
        "spec": {"rules": [{"left": "funding_rate", "operator": ">", "right": 0}]},
    }

    assert auto_trim._extract_referenced_columns(params=params) == {"funding_rate"}


def test_maybe_select_window_clamps_explicit_dates_to_availability(monkeypatch):
    def _availability(**_kwargs):
        return {
            "usable": True,
            "start": "2026-02-01T00:00:00Z",
            "end": "2026-03-01T00:00:00Z",
            "columns": ["funding_rate"],
        }

    monkeypatch.setattr(auto_trim, "compute_data_availability", _availability)
    monkeypatch.setattr(
        auto_trim.dt,
        "datetime",
        type(
            "FrozenDateTime",
            (auto_trim.dt.datetime,),
            {"now": classmethod(lambda cls, tz=None: auto_trim.dt.datetime(2026, 3, 10, tzinfo=tz))},
        ),
    )

    start, end, _availability = auto_trim.maybe_select_window(
        strategy_type="funding_strategy",
        params={},
        symbol="BTC",
        timeframe="1h",
        explicit_start="2026-01-01T00:00:00Z",
        explicit_end="2026-04-01T00:00:00Z",
    )

    assert start == "2026-02-01T00:00:00Z"
    assert end == "2026-03-01T00:00:00Z"


def test_compute_data_availability_blocks_missing_referenced_columns(monkeypatch):
    def _best_entry(_asset, metric, _timeframe):
        if metric == "funding_rate":
            return {
                "from": "2026-01-01T00:00:00+00:00",
                "to": "2026-03-01T00:00:00+00:00",
                "points": 100,
                "interval": "1h",
            }
        return None

    monkeypatch.setattr(auto_trim, "_resolve_asset", lambda _symbol: "bitcoin")
    monkeypatch.setattr(auto_trim, "_ensure_loaded", lambda: None)
    monkeypatch.setattr(auto_trim, "_LOOKUP", {"present": True})
    monkeypatch.setattr(auto_trim, "_best_entry", _best_entry)

    availability = auto_trim.compute_data_availability(
        asset="BTC",
        timeframe="1h",
        columns={"funding_rate", "open_interest"},
    )

    assert availability["usable"] is False
    assert availability["missing_columns"] == ["open_interest"]
    assert "missing range data" in availability["summary"]


def test_backtest_auto_trim_source_resolver_reads_registered_class_source():
    from axiom.strategies.backtest import _read_strategy_source_for_auto_trim

    class SourceResolverProbe:
        def generate_signal(self, df):
            return df["funding_rate"].iloc[-1] > 0

    source = _read_strategy_source_for_auto_trim(SourceResolverProbe)

    assert source is not None
    assert 'df["funding_rate"]' in source


def test_backtest_caps_direct_bar_requests(monkeypatch):
    import numpy as np
    import pandas as pd
    import sys
    import types

    from axiom.strategies import backtest as backtest_mod

    api_core_stub = types.ModuleType("axiom.api_core")
    api_core_stub.get_settings = lambda: {}
    monkeypatch.setitem(sys.modules, "axiom.api_core", api_core_stub)

    captured = {}
    frame = pd.DataFrame(
        {
            "open": np.ones(320),
            "high": np.ones(320) + 1,
            "low": np.ones(320) - 1,
            "close": np.ones(320),
            "volume": np.ones(320),
        },
        index=pd.date_range("2026-01-01", periods=320, freq="h", tz="UTC"),
    )

    def _load_backtest_candles(**kwargs):
        captured.update(kwargs)
        return frame

    monkeypatch.setattr(backtest_mod, "load_backtest_candles", _load_backtest_candles)
    monkeypatch.setattr(backtest_mod, "_should_use_process_isolation", lambda: False)

    backtest_mod.backtest_strategy(
        strategy_id="cap-direct",
        asset="BTC",
        strategy_type="rsi_momentum",
        params={},
        bars=100_000,
        persist_legacy_run=False,
        sync_strategy_state=False,
    )

    assert captured["bars"] == 15_000


def test_walk_forward_uses_auto_trim_window_and_caps_bars(monkeypatch):
    import numpy as np
    import pandas as pd
    import sys
    import types

    from axiom.strategies import backtest as backtest_mod

    api_core_stub = types.ModuleType("axiom.api_core")
    api_core_stub.get_settings = lambda: {}
    monkeypatch.setitem(sys.modules, "axiom.api_core", api_core_stub)

    captured = {}
    frame = pd.DataFrame(
        {
            "open": np.ones(1_000),
            "high": np.ones(1_000) + 1,
            "low": np.ones(1_000) - 1,
            "close": np.ones(1_000),
            "volume": np.ones(1_000),
        },
        index=pd.date_range("2026-01-01", periods=1_000, freq="h", tz="UTC"),
    )

    def _load_backtest_candles(**kwargs):
        captured.update(kwargs)
        return frame

    def _maybe_select_window(**kwargs):
        return (
            "2026-02-01T00:00:00Z",
            None,
            {"usable": True, "columns": ["funding_rate"], "summary": "trimmed"},
        )

    monkeypatch.setattr("axiom.auto_trim.maybe_select_window", _maybe_select_window)
    monkeypatch.setattr(backtest_mod, "load_backtest_candles", _load_backtest_candles)
    monkeypatch.setattr(backtest_mod, "_should_use_process_isolation", lambda: False)
    monkeypatch.setattr(backtest_mod, "_run_signal_walk", lambda *args, **kwargs: [])

    result = backtest_mod.walk_forward(
        strategy_id="wf-cap-trim",
        asset="BTC",
        strategy_type="rsi_momentum",
        params={},
        total_bars=100_000,
        n_splits=2,
    )

    assert captured["bars"] == 30_000
    assert captured["start_date"] == "2026-02-01T00:00:00Z"
    assert result["data_availability"]["columns"] == ["funding_rate"]
