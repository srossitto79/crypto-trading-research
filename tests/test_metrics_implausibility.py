"""Implausible-metrics quarantine (the look-ahead / data-leak fingerprint).

`check_metrics_integrity` now flags clamped/implausible Sharpe, PF, and return
values and routes them to the non-terminal `DataQualityHold` quarantine. This
runs on the persist path + both brain transition guardrails, which are NOT
bypassed under `testing_mode` (the policy gate's plausibility check is), so a
clamped-Sharpe leak can no longer enqueue while gates are relaxed.
"""
from __future__ import annotations

from axiom.metrics_integrity import (
    check_metrics_integrity,
    data_quality_hold_reason,
    DATA_QUALITY_HOLD_PREFIX,
)


def _metrics(is_sharpe, oos_sharpe, *, is_tr=60, oos_tr=30, pf=1.5, ret=0.05):
    def leg(s, t):
        return {"sharpe": s, "total_trades": t, "profit_factor": pf, "total_return_pct": ret}
    return {"in_sample": leg(is_sharpe, is_tr), "out_of_sample": leg(oos_sharpe, oos_tr)}


def test_clamped_sharpe_is_quarantined_non_terminally():
    anomalies = check_metrics_integrity(_metrics(10.0, 9.99))
    assert anomalies and any("clamp" in a for a in anomalies)
    reason = data_quality_hold_reason(anomalies)
    assert reason.startswith(DATA_QUALITY_HOLD_PREFIX)
    assert "(reject)" not in reason  # must be NON-terminal (held, not archived)


def test_negative_clamp_is_quarantined():
    assert check_metrics_integrity(_metrics(-10.0, 1.0))


def test_implausibly_high_but_unclamped_sharpe_flagged():
    # The exact S02940 leak fingerprint (IS 6.82 / OOS 5.52) -- caught at |Sharpe|>=6.
    assert check_metrics_integrity(_metrics(6.82, 5.52))


def test_high_pf_on_real_sample_flagged():
    assert check_metrics_integrity(_metrics(1.0, 1.0, pf=12.0))


def test_high_pf_on_tiny_sample_is_noise_not_flagged():
    # A high PF on a handful of trades is small-sample noise, not a leak.
    assert check_metrics_integrity(_metrics(1.0, 1.0, is_tr=3, oos_tr=2, pf=12.0)) == []


def test_absurd_return_flagged():
    assert check_metrics_integrity(_metrics(1.0, 1.0, ret=24000.0))  # millions-% leak


def test_normal_metrics_pass_clean():
    assert check_metrics_integrity(_metrics(1.2, 0.9, pf=1.6, ret=0.08)) == []
    assert check_metrics_integrity(_metrics(0.07, 2.19, pf=1.96, ret=0.30)) == []  # real R2 winner S02754


def test_existing_zero_trade_anomaly_preserved():
    assert check_metrics_integrity(_metrics(0.0, 0.0, is_tr=0, oos_tr=30))
