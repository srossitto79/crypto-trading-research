"""Family-name guardrail: create_strategy rejections suggest real alternatives.

When an agent invents an unregistered TYPE_NAME (e.g. `rsi_atr_regime_momentum`),
the rejection message now names close-matching existing families so the agent
has a concrete next step instead of making up another new name.
"""
from __future__ import annotations

from axiom.agents.tools_brain import _suggest_known_families
from axiom.strategies.params import SUPPORTED_PARAM_FAMILIES


def test_empty_input_returns_no_suggestions():
    assert _suggest_known_families("") == []
    assert _suggest_known_families(None) == []


def test_close_match_returns_real_family():
    # The real case that prompted this fix: agent tried to create
    # rsi_atr_regime_momentum (not a family); rsi_momentum IS a family.
    suggestions = _suggest_known_families("rsi_atr_regime_momentum")
    assert suggestions, "expected at least one suggestion"
    assert "rsi_momentum" in suggestions


def test_suggestions_are_always_real_families():
    for probe in ["ema_crossover_vol", "volatility_breakout", "mean_reversion_bb"]:
        for suggestion in _suggest_known_families(probe):
            assert suggestion in SUPPORTED_PARAM_FAMILIES


def test_completely_unrelated_input_returns_empty():
    assert _suggest_known_families("zzzz_quantum_unicorn_9999") == []


def test_suggestion_count_capped_at_n():
    suggestions = _suggest_known_families("reversion_atr_thing", n=2)
    assert len(suggestions) <= 2
