"""Test that adx_max is a recognized strategy parameter."""

from axiom.strategies.params import _COMMON_ALLOWED_PARAMS, canonicalize_params


def test_adx_max_in_common_allowed_params():
    """adx_max should be whitelisted alongside adx_min."""
    assert "adx_max" in _COMMON_ALLOWED_PARAMS
    assert "adx_min" in _COMMON_ALLOWED_PARAMS


def test_adx_max_not_flagged_as_unknown():
    """adx_max should not appear in unknown_params after canonicalization."""
    result = canonicalize_params("rsi_momentum", {"adx_max": 50, "adx_min": 20, "adx_period": 14})
    assert "adx_max" not in result.unknown_params
    assert result.params["adx_max"] == 50
