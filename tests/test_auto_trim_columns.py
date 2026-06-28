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


def test_backtest_auto_trim_source_resolver_reads_registered_class_source():
    from axiom.strategies.backtest import _read_strategy_source_for_auto_trim

    class SourceResolverProbe:
        def generate_signal(self, df):
            return df["funding_rate"].iloc[-1] > 0

    source = _read_strategy_source_for_auto_trim(SourceResolverProbe)

    assert source is not None
    assert 'df["funding_rate"]' in source
