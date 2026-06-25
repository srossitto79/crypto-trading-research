from axiom.selfheal import validate_strategy_code


def test_validate_strategy_code_handles_utf8_harness_on_windows():
    code = """
from axiom.strategies.base import BaseStrategy


class ExampleStrategy(BaseStrategy):
    @property
    def name(self):
        return "Example"

    @property
    def asset(self):
        return "BTC"

    @property
    def strategy_type(self):
        return "example_strategy"

    @property
    def default_params(self):
        return {}

    def generate_signal(self, df):
        return 0


STRATEGY_CLASS = ExampleStrategy
TYPE_NAME = "example_strategy"
"""

    result = validate_strategy_code(code)

    assert isinstance(result, dict)
    stderr = str(result.get("execution_result", {}).get("stderr") or "").lower()
    assert "charmap" not in stderr
    assert "no module named 'Axiom'" not in stderr
