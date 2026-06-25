"""Tests for Axiom.cost_pricing — per-model USD cost estimation."""

from axiom.cost_pricing import estimate_cost_usd


def test_openai_gpt4o_basic():
    # 1M input @ $2.50, 1M output @ $10.00
    cost = estimate_cost_usd("openai", "gpt-4o", {"input_tokens": 1_000_000, "output_tokens": 1_000_000})
    assert cost == 12.50


def test_openai_gpt4o_mini_small_call():
    # 1k input @ $0.15/1M, 500 output @ $0.60/1M
    cost = estimate_cost_usd("openai", "gpt-4o-mini", {"input_tokens": 1000, "output_tokens": 500})
    expected = (1000 * 0.15 + 500 * 0.60) / 1_000_000
    assert abs(cost - expected) < 1e-9


def test_zero_usage():
    assert estimate_cost_usd("openai", "gpt-4o", {"input_tokens": 0, "output_tokens": 0}) == 0.0


def test_none_usage():
    assert estimate_cost_usd("openai", "gpt-4o", None) == 0.0


def test_unknown_model_returns_zero():
    """Unknown (provider, model) — return 0.0, don't raise."""
    cost = estimate_cost_usd("openai", "made-up-model-9000", {"input_tokens": 1000, "output_tokens": 1000})
    assert cost == 0.0


def test_unknown_provider_returns_zero():
    cost = estimate_cost_usd("anthropic", "claude-sonnet-4", {"input_tokens": 1000, "output_tokens": 1000})
    assert cost == 0.0


def test_alternate_usage_keys():
    """OpenAI returns prompt_tokens / completion_tokens — accept both shapes."""
    cost = estimate_cost_usd(
        "openai", "gpt-4o",
        {"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000},
    )
    assert cost == 12.50


def test_lmstudio_local_is_free():
    cost = estimate_cost_usd("lmstudio", "local-model", {"input_tokens": 100_000, "output_tokens": 100_000})
    assert cost == 0.0


def test_minimax_pricing():
    # MiniMax-M2.5: $0.30 in / $1.50 out per 1M
    cost = estimate_cost_usd("minimax", "MiniMax-M2.5", {"input_tokens": 1_000_000, "output_tokens": 1_000_000})
    assert cost == round(0.30 + 1.50, 6)


def test_zai_pricing():
    cost = estimate_cost_usd("zai", "glm-4.5", {"input_tokens": 1_000_000, "output_tokens": 0})
    assert cost == 0.50


def test_case_insensitive_model_id():
    cost = estimate_cost_usd("minimax", "minimax-m2.5", {"input_tokens": 1_000_000, "output_tokens": 0})
    assert cost == 0.30


def test_openrouter_falls_back_to_vendor():
    """OpenRouter routes openai/gpt-4o → openai pricing."""
    cost = estimate_cost_usd(
        "openrouter", "openai/gpt-4o",
        {"input_tokens": 1_000_000, "output_tokens": 0},
    )
    assert cost == 2.50


def test_openrouter_unknown_vendor_returns_zero():
    cost = estimate_cost_usd(
        "openrouter", "anthropic/claude-sonnet-4",
        {"input_tokens": 1000, "output_tokens": 1000},
    )
    # No anthropic entries in our table — graceful zero.
    assert cost == 0.0


def test_openrouter_no_slash_returns_zero():
    """Plain model id under openrouter without vendor/ prefix → unknown."""
    cost = estimate_cost_usd("openrouter", "gpt-4o", {"input_tokens": 1000, "output_tokens": 0})
    assert cost == 0.0


def test_provider_normalization_strips_whitespace():
    cost = estimate_cost_usd("  OpenAI  ", "gpt-4o", {"input_tokens": 1_000_000, "output_tokens": 0})
    assert cost == 2.50


def test_returns_float_rounded_to_6_decimals():
    cost = estimate_cost_usd("openai", "gpt-4o-mini", {"input_tokens": 1, "output_tokens": 1})
    assert isinstance(cost, float)
    assert cost == round(cost, 6)
