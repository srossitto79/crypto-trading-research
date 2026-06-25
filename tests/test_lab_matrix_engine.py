from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pandas as pd

from axiom.db import get_db
from axiom.lab_db import (
    create_lab_experiment,
    create_or_update_model_version,
    enqueue_lab_job,
    get_regime_container_snapshot,
    replace_regime_segments,
    upsert_snapshot_manifest,
)
from axiom.lab_matrix_engine import (
    MATRIX_JOB_TYPE,
    StrategyCandidate,
    _build_candidate_payload,
    _candidate_to_score_row,
    _load_strategy_candidates,
    _ranked_selection_pool,
    _score_row_to_candidate,
    _select_champion_with_guardrails,
    apply_regime_execution_penalty,
    evaluate_admission_gates,
    process_next_matrix_job,
    resolve_min_matrix_window_bars,
)


def _window(start: str, bars: int, close_base: float) -> pd.DataFrame:
    timestamps = pd.date_range(start, periods=bars, freq="h", tz="UTC")
    return pd.DataFrame(
        {
            "timestamp": timestamps,
            "open": [close_base + 0.1] * bars,
            "high": [close_base + 0.5] * bars,
            "low": [close_base - 0.5] * bars,
            "close": [close_base] * bars,
            "volume": [1000.0] * bars,
        }
    )


def test_execution_penalty_is_higher_in_high_drag_regimes():
    trade_returns = [0.03, -0.01, 0.02, 0.01, -0.005]
    raw_metrics = {"stability": 0.7}

    low_drag = apply_regime_execution_penalty(
        regime="TREND_UP",
        raw_metrics=raw_metrics,
        trade_returns=trade_returns,
    )
    high_drag = apply_regime_execution_penalty(
        regime="HIGH_VOL",
        raw_metrics=raw_metrics,
        trade_returns=trade_returns,
    )

    assert low_drag["execution_penalty_multiplier"] == 1.0
    assert high_drag["execution_penalty_multiplier"] > 1.0
    assert high_drag["execution_drag_per_trade"] > low_drag["execution_drag_per_trade"]
    assert high_drag["total_return_pct"] < low_drag["total_return_pct"]


def test_admission_gates_reject_unqualified_strategy():
    failing_metrics = {
        "total_trades": 18.0,
        "oos_forward_total_trades": 12.0,
        "total_return_pct": 0.04,
        "profit_factor": 1.05,
        "sharpe": 0.22,
        "max_drawdown_pct": 0.42,
        "oos_forward_total_return_pct": -0.01,
        "oos_forward_profit_factor": 0.98,
    }
    decision = evaluate_admission_gates(failing_metrics)

    assert decision["admitted"] is False
    assert decision["checks"]["trades_gte_75"] is False
    assert decision["checks"]["oos_trades_gte_30"] is False
    assert decision["checks"]["profit_factor_gte_1_10"] is False
    assert decision["checks"]["sharpe_gte_0_30"] is False
    assert decision["checks"]["max_drawdown_lte_0_35"] is False
    assert decision["checks"]["oos_forward_return_non_negative"] is False
    assert decision["checks"]["oos_forward_pf_gte_1_0"] is False
    assert decision["fallback_eligible"] is False


def test_admission_gates_allow_fallback_when_only_sample_counts_fail():
    near_miss_metrics = {
        "total_trades": 20.0,
        "oos_forward_total_trades": 10.0,
        "total_return_pct": 0.095,
        "profit_factor": 1.84,
        "sharpe": 0.83,
        "max_drawdown_pct": 0.07,
        "oos_forward_total_return_pct": 0.003,
        "oos_forward_profit_factor": 1.12,
    }

    decision = evaluate_admission_gates(near_miss_metrics)

    assert decision["admitted"] is False
    assert decision["checks"]["trades_gte_75"] is False
    assert decision["checks"]["oos_trades_gte_30"] is False
    assert decision["fallback_eligible"] is True
    assert decision["relaxed_checks"]["trades_gte_10"] is True
    assert decision["relaxed_checks"]["oos_trades_gte_5"] is True
    assert decision["relaxed_checks"]["strict_non_sample_pass"] is True


def test_admission_gates_allow_borderline_oos_validated_fallback():
    borderline_metrics = {
        "total_trades": 34.0,
        "oos_forward_total_trades": 38.0,
        "total_return_pct": -0.03,
        "profit_factor": 0.986,
        "sharpe": -0.03,
        "max_drawdown_pct": 0.18,
        "oos_forward_total_return_pct": 0.156,
        "oos_forward_profit_factor": 1.31,
    }

    decision = evaluate_admission_gates(borderline_metrics)

    assert decision["admitted"] is False
    assert decision["fallback_eligible"] is False
    assert decision["borderline_eligible"] is True
    assert decision["borderline_checks"]["post_cost_return_gte_neg_0_05"] is True
    assert decision["borderline_checks"]["profit_factor_gte_0_95"] is True
    assert decision["borderline_checks"]["sharpe_gte_neg_0_05"] is True
    assert decision["borderline_checks"]["oos_forward_return_non_negative"] is True
    assert decision["borderline_checks"]["oos_forward_pf_gte_1_0"] is True


def test_ranked_selection_pool_uses_fallback_candidates_when_strict_pool_is_empty():
    fallback_candidate = {
        "strategy_id": "S-FALLBACK",
        "score": 0.42,
        "admission": {"admitted": False, "fallback_eligible": True},
    }
    rejected_candidate = {
        "strategy_id": "S-REJECT",
        "score": 0.0,
        "admission": {"admitted": False, "fallback_eligible": False},
    }

    selected, mode = _ranked_selection_pool([rejected_candidate, fallback_candidate])

    assert mode == "fallback_sampling_shortfall"
    assert selected == [fallback_candidate]


def test_ranked_selection_pool_uses_borderline_candidates_after_other_pools():
    borderline_candidate = {
        "strategy_id": "S-BORDERLINE",
        "score": 0.31,
        "admission": {"admitted": False, "fallback_eligible": False, "borderline_eligible": True},
    }
    rejected_candidate = {
        "strategy_id": "S-REJECT",
        "score": 0.0,
        "admission": {"admitted": False, "fallback_eligible": False, "borderline_eligible": False},
    }

    selected, mode = _ranked_selection_pool([rejected_candidate, borderline_candidate])

    assert mode == "fallback_borderline_validated"
    assert selected == [borderline_candidate]


def test_resolve_min_matrix_window_bars_is_timeframe_aware():
    assert resolve_min_matrix_window_bars("15m") == 96
    assert resolve_min_matrix_window_bars("30m") == 120
    assert resolve_min_matrix_window_bars("1h") == 168
    assert resolve_min_matrix_window_bars("1d") == 210


def test_candidate_payload_uses_true_forward_oos(monkeypatch):
    strategy = StrategyCandidate(strategy_id="S1", strategy_type="ema_cross", params={})
    train_window = _window("2026-01-01T00:00:00Z", 240, 100.0)
    oos_window = _window("2026-02-01T00:00:00Z", 240, 300.0)

    def _fake_backtest_strategy(*, candles_df, **_kwargs):
        first_close = float(candles_df["close"].iloc[0])
        if first_close < 200:
            pnl = 0.015
        else:
            pnl = -0.02
        trades = [{"pnl_pct": pnl, "entry_time": str(candles_df.index[0]), "exit_time": str(candles_df.index[-1])}] * 40
        return {
            "is_trades": trades[:20],
            "oos_trades": trades[20:],
            "is_metrics": {"robustness": 0.8},
            "oos_metrics": {"robustness": 0.6},
        }

    monkeypatch.setattr("axiom.lab_matrix_engine.backtest_strategy", _fake_backtest_strategy)

    candidate = _build_candidate_payload(
        regime="TREND_UP",
        strategy=strategy,
        train_window=train_window,
        oos_window=oos_window,
        symbol="BTC/USDT",
        timeframe="1h",
    )

    assert candidate["raw_metrics"]["total_return_pct"] > 0
    assert candidate["oos_raw_metrics"]["total_return_pct"] < 0
    assert candidate["adjusted_metrics"]["oos_forward_total_return_pct"] < 0
    assert candidate["admission"]["admitted"] is False
    assert candidate["admission"]["checks"]["oos_forward_return_non_negative"] is False


def test_candidate_payload_skips_large_non_vectorized_windows(monkeypatch):
    strategy = StrategyCandidate(
        strategy_id="SLOW-1",
        strategy_type="slow_custom",
        params={},
        supports_vectorized_signals=False,
    )
    train_window = _window("2026-01-01T00:00:00Z", 10001, 100.0)
    oos_window = _window("2026-03-01T00:00:00Z", 10001, 101.0)
    calls = {"count": 0}

    def _fake_backtest_strategy(**_kwargs):
        calls["count"] += 1
        return {}

    monkeypatch.setattr("axiom.lab_matrix_engine.backtest_strategy", _fake_backtest_strategy)

    candidate = _build_candidate_payload(
        regime="TREND_UP",
        strategy=strategy,
        train_window=train_window,
        oos_window=oos_window,
        symbol="BTC/USDT",
        timeframe="1h",
    )

    assert calls["count"] == 0
    assert candidate["admission"]["admitted"] is False
    assert "train_non_vectorized_large_window" in candidate["admission"]["reasons"]
    assert "oos_non_vectorized_large_window" in candidate["admission"]["reasons"]
    assert "non-vectorized" in str(candidate["diagnostics"]["train"]["error"]).lower()


def test_load_strategy_candidates_merges_registry_and_managed_sources(monkeypatch):
    class _RegistryStrategy:
        strategy_type = "ADX_TREND"
        params = {"adx_length": 14, "adx_threshold": 25}
        name = "Registry EMA"

        def generate_signals(self, df):
            return None

    monkeypatch.setattr("axiom.lab_matrix_engine.discover", lambda: None)
    monkeypatch.setattr("axiom.lab_matrix_engine.get_all", lambda: {"REG-1": _RegistryStrategy()})
    monkeypatch.setattr(
        "axiom.lab_matrix_engine.list_strategy_pool_candidates",
        lambda **_kwargs: [
            {
                "strategy_id": "MAN-1",
                "strategy_type": "williams_r",
                "params": {"williams_length": 14, "oversold": -80, "overbought": -20},
                "supports_vectorized_signals": False,
                "source_pool": "graveyard",
                "source_stage": "archived",
                "display_name": "Managed ADL",
            }
        ],
    )

    rows = _load_strategy_candidates(
        strategy_sources=["registry", "graveyard"],
        max_strategies=10,
    )

    assert [row.strategy_id for row in rows] == ["REG-1", "MAN-1"]
    assert rows[0].source_pool == "registry"
    assert rows[0].supports_vectorized_signals is True
    assert rows[1].source_pool == "graveyard"
    assert rows[1].supports_vectorized_signals is False


def test_load_strategy_candidates_respects_source_order_when_truncated(monkeypatch):
    class _RegistryStrategy:
        strategy_type = "williams_r"
        params = {"williams_length": 14}
        name = "Registry Mean Reversion"

        def generate_signals(self, df):
            return None

    monkeypatch.setattr("axiom.lab_matrix_engine.discover", lambda: None)
    monkeypatch.setattr("axiom.lab_matrix_engine.get_all", lambda: {"REG-1": _RegistryStrategy()})

    def _fake_list_strategy_pool_candidates(*, strategy_sources, **_kwargs):
        source = strategy_sources[0]
        if source == "active":
            return [
                {
                    "strategy_id": "ACT-1",
                    "strategy_type": "ema_cross",
                    "params": {"fast": 10, "slow": 20},
                    "supports_vectorized_signals": True,
                    "source_pool": "active",
                    "source_stage": "paper",
                    "display_name": "Active Trend",
                }
            ]
        if source == "graveyard":
            return [
                {
                    "strategy_id": "GRV-1",
                    "strategy_type": "atr_breakout",
                    "params": {"atr_length": 14},
                    "supports_vectorized_signals": False,
                    "source_pool": "graveyard",
                    "source_stage": "rejected",
                    "display_name": "Graveyard Breakout",
                }
            ]
        return []

    monkeypatch.setattr(
        "axiom.lab_matrix_engine.list_strategy_pool_candidates",
        _fake_list_strategy_pool_candidates,
    )

    rows = _load_strategy_candidates(
        strategy_sources=["active", "registry", "graveyard"],
        max_strategies=2,
    )

    assert [(row.strategy_id, row.source_pool) for row in rows] == [
        ("ACT-1", "active"),
        ("REG-1", "registry"),
    ]


def test_champion_guardrails_hold_previous_on_small_delta():
    selected_at = (datetime.now(UTC) - timedelta(hours=6)).isoformat()
    previous_snapshot = {
        "meta_json": {"champion_selected_at": selected_at},
        "champion": {"strategy_id": "ACTIVE-1", "created_at": selected_at},
        "members": [],
    }
    scored = [
        {
            "strategy_id": "CHALLENGER-1",
            "score": 0.84,
            "strategy_source": "active",
            "strategy_stage": "paper",
            "strategy_name": "Challenger",
            "score_components": {"sharpe_norm": 1.0},
            "admission": {"checks": {"profit_factor_gte_1_10": True}},
            "oos_adjusted_metrics": {"profit_factor": 1.3},
            "rank": 1,
        },
        {
            "strategy_id": "ACTIVE-1",
            "score": 0.80,
            "strategy_source": "active",
            "strategy_stage": "paper",
            "strategy_name": "Current Champion",
            "score_components": {"sharpe_norm": 0.9},
            "admission": {"checks": {"profit_factor_gte_1_10": True}},
            "oos_adjusted_metrics": {"profit_factor": 1.2},
            "rank": 2,
        },
    ]

    champion, reserves, meta = _select_champion_with_guardrails(
        regime="TREND_UP",
        scored=scored,
        previous_snapshot=previous_snapshot,
        reserve_count=2,
        min_champion_dwell_hours=24,
        min_champion_score_delta=0.08,
        graveyard_required_wins=2,
    )

    assert champion is not None
    assert champion["strategy_id"] == "ACTIVE-1"
    assert meta["selection_reason"] == "held_min_dwell"
    assert reserves[0]["strategy_id"] == "CHALLENGER-1"


def test_champion_guardrails_require_repeated_graveyard_wins():
    previous_snapshot = {
        "meta_json": {"champion_selected_at": "2026-03-10T10:00:00+00:00"},
        "champion": {"strategy_id": "ACTIVE-1", "created_at": "2026-03-10T10:00:00+00:00"},
        "members": [],
    }
    scored = [
        {
            "strategy_id": "GRAVE-1",
            "score": 0.91,
            "strategy_source": "graveyard",
            "strategy_stage": "archived",
            "strategy_name": "Graveyard Winner",
            "score_components": {"sharpe_norm": 1.0},
            "admission": {"checks": {"profit_factor_gte_1_10": True}},
            "oos_adjusted_metrics": {"profit_factor": 1.4},
            "rank": 1,
        },
        {
            "strategy_id": "ACTIVE-1",
            "score": 0.81,
            "strategy_source": "active",
            "strategy_stage": "paper",
            "strategy_name": "Current Champion",
            "score_components": {"sharpe_norm": 0.9},
            "admission": {"checks": {"profit_factor_gte_1_10": True}},
            "oos_adjusted_metrics": {"profit_factor": 1.2},
            "rank": 2,
        },
    ]

    champion, reserves, meta = _select_champion_with_guardrails(
        regime="RANGE",
        scored=scored,
        previous_snapshot=previous_snapshot,
        reserve_count=2,
        min_champion_dwell_hours=24,
        min_champion_score_delta=0.08,
        graveyard_required_wins=2,
    )

    assert champion is not None
    assert champion["strategy_id"] == "ACTIVE-1"
    assert meta["selection_reason"] == "graveyard_pending_validation"
    assert meta["pending_graveyard_candidate_id"] == "GRAVE-1"
    assert meta["pending_graveyard_wins"] == 1
    assert reserves[0]["strategy_id"] == "GRAVE-1"


def test_champion_guardrails_promote_graveyard_after_nested_pending_validation():
    previous_snapshot = {
        "meta_json": {
            "champion_selection": {
                "selection_reason": "graveyard_pending_validation",
                "pending_graveyard_candidate_id": "GRAVE-1",
                "pending_graveyard_wins": 1,
                "graveyard_required_wins": 2,
                "champion_selected_at": None,
            }
        },
        "champion": None,
        "members": [],
    }
    scored = [
        {
            "strategy_id": "GRAVE-1",
            "score": 0.91,
            "strategy_source": "graveyard",
            "strategy_stage": "archived",
            "strategy_name": "Graveyard Winner",
            "score_components": {"sharpe_norm": 1.0},
            "admission": {"checks": {"profit_factor_gte_1_10": True}},
            "oos_adjusted_metrics": {"profit_factor": 1.4},
            "rank": 1,
        }
    ]

    champion, reserves, meta = _select_champion_with_guardrails(
        regime="TREND_UP",
        scored=scored,
        previous_snapshot=previous_snapshot,
        reserve_count=2,
        min_champion_dwell_hours=24,
        min_champion_score_delta=0.08,
        graveyard_required_wins=2,
    )

    assert champion is not None
    assert champion["strategy_id"] == "GRAVE-1"
    assert meta["selection_reason"] == "graveyard_promoted"
    assert meta["pending_graveyard_candidate_id"] == "GRAVE-1"
    assert meta["pending_graveyard_wins"] == 2
    assert reserves == []


def test_matrix_job_pools_short_segments_into_regime_windows(AXIOM_db, monkeypatch, tmp_path):
    experiment = create_lab_experiment(
        experiment_id=f"exp_{uuid4().hex[:8]}",
        symbol="BTC/USDT",
        timeframe="1h",
        regime_timeframe="1h",
        execution_timeframe="15m",
        train_start="2026-01-01T00:00:00Z",
        train_end="2026-01-20T23:00:00Z",
        test_start="2026-02-01T00:00:00Z",
        test_end="2026-02-20T23:00:00Z",
        status="ready",
    )
    model = create_or_update_model_version(
        version_key=f"mv_{uuid4().hex[:8]}",
        experiment_id=experiment.id,
        status="active",
    )

    regime_snapshot = pd.concat(
        [
            _window("2026-01-01T00:00:00Z", 500, 100.0),
            _window("2026-02-01T00:00:00Z", 500, 101.0),
        ],
        ignore_index=True,
    ).drop_duplicates(subset=["timestamp"], keep="last")
    snapshot_path = tmp_path / "matrix_snapshot.parquet"
    regime_snapshot.to_parquet(snapshot_path, index=False)
    upsert_snapshot_manifest(
        experiment_id=experiment.id,
        snapshot_path=str(snapshot_path),
        snapshot_hash="hash-test",
        symbol=experiment.symbol,
        timeframe=experiment.regime_timeframe,
        row_count=len(regime_snapshot),
        coverage_start=str(regime_snapshot["timestamp"].iloc[0]),
        coverage_end=str(regime_snapshot["timestamp"].iloc[-1]),
        manifest_json={},
    )
    replace_regime_segments(
        model_version_id=model.id,
        symbol=experiment.symbol,
        timeframe=experiment.timeframe,
        segments=[
            {
                "regime": "TREND_UP_LOW_VOL",
                "segment_start": "2026-01-01T00:00:00Z",
                "segment_end": "2026-01-05T23:00:00Z",
                "confidence_avg": 0.8,
                "bars_count": 120,
                "meta_json": {},
            },
            {
                "regime": "TREND_UP_LOW_VOL",
                "segment_start": "2026-01-10T00:00:00Z",
                "segment_end": "2026-01-14T23:00:00Z",
                "confidence_avg": 0.82,
                "bars_count": 120,
                "meta_json": {},
            },
            {
                "regime": "TREND_UP_LOW_VOL",
                "segment_start": "2026-02-01T00:00:00Z",
                "segment_end": "2026-02-05T23:00:00Z",
                "confidence_avg": 0.79,
                "bars_count": 120,
                "meta_json": {},
            },
            {
                "regime": "TREND_UP_LOW_VOL",
                "segment_start": "2026-02-10T00:00:00Z",
                "segment_end": "2026-02-14T23:00:00Z",
                "confidence_avg": 0.81,
                "bars_count": 120,
                "meta_json": {},
            },
        ],
    )

    monkeypatch.setattr(
        "axiom.lab_matrix_engine._load_strategy_candidates",
        lambda *args, **kwargs: [StrategyCandidate(strategy_id="SPOOL", strategy_type="ema_cross", params={})],
    )

    observed_lengths: list[int] = []
    execution_snapshot = pd.concat(
        [
            _window("2026-01-01T00:00:00Z", 2000, 100.0).assign(
                timestamp=lambda df: pd.date_range("2026-01-01T00:00:00Z", periods=2000, freq="15min", tz="UTC")
            ),
            _window("2026-02-01T00:00:00Z", 2000, 101.0).assign(
                timestamp=lambda df: pd.date_range("2026-02-01T00:00:00Z", periods=2000, freq="15min", tz="UTC")
            ),
        ],
        ignore_index=True,
    ).drop_duplicates(subset=["timestamp"], keep="last")

    def _fake_backtest_strategy(*, candles_df, **_kwargs):
        observed_lengths.append(len(candles_df))
        trades = [
            {"pnl_pct": 0.01, "entry_time": f"a{i}", "exit_time": f"b{i}"}
            for i in range(80)
        ]
        return {
            "is_trades": trades[:40],
            "oos_trades": trades[40:],
            "is_metrics": {"robustness": 0.9},
            "oos_metrics": {"robustness": 0.8},
        }

    monkeypatch.setattr("axiom.lab_matrix_engine.backtest_strategy", _fake_backtest_strategy)
    monkeypatch.setattr("axiom.lab_matrix_engine.load_parquet", lambda *_args, **_kwargs: execution_snapshot.copy())

    enqueue_lab_job(
        job_type=MATRIX_JOB_TYPE,
        experiment_id=experiment.id,
        payload={"model_version_id": model.id, "score_version": "v2"},
    )
    summary = process_next_matrix_job(worker_id="test-worker")

    assert summary is not None
    assert observed_lengths[:2] == [954, 954]
    assert summary["containers_persisted"] == 0
    assert summary["pending_approval_id"] is not None
    container = get_regime_container_snapshot(model.id, "TREND_UP")
    assert container is None

    with get_db() as conn:
        approval = conn.execute(
            "SELECT payload FROM approvals WHERE id = ?",
            (summary["pending_approval_id"],),
        ).fetchone()

    payload = json.loads(approval["payload"])
    container_payload = payload["container_payloads"][0]
    assert container_payload["champion"] is not None
    assert container_payload["meta_json"]["regime_timeframe"] == "1h"
    assert container_payload["meta_json"]["execution_timeframe"] == "15m"
    assert container_payload["meta_json"]["train_bars"] == 954
    assert container_payload["meta_json"]["oos_bars"] == 954
    assert isinstance(container_payload["meta_json"]["reserves"], list)
    assert isinstance(container_payload["meta_json"]["champion_selection"], dict)


def test_load_strategy_candidates_expands_registry_trade_mode_variants(monkeypatch):
    class _VariantRegistryStrategy:
        strategy_type = "williams_r"
        params = {"williams_length": 14}
        name = "Variant Registry"
        supported_trade_modes = {"long_only", "both"}

        def generate_signals(self, df):
            return None

    monkeypatch.setattr("axiom.lab_matrix_engine.discover", lambda: None)
    monkeypatch.setattr("axiom.lab_matrix_engine.get_all", lambda: {"REG-VAR": _VariantRegistryStrategy()})
    monkeypatch.setattr("axiom.lab_matrix_engine.list_strategy_pool_candidates", lambda **_kwargs: [])

    rows = _load_strategy_candidates(strategy_sources=["registry"])

    assert [(row.strategy_id, row.trade_mode) for row in rows] == [
        ("REG-VAR", "long_only"),
        ("REG-VAR", "short_only"),
        ("REG-VAR", "both"),
    ]
    assert [row.candidate_key for row in rows] == [
        "REG-VAR:long_only",
        "REG-VAR:short_only",
        "REG-VAR:both",
    ]
    assert rows[-1].position_model == "hedged"


def test_score_row_round_trips_candidate_key_and_trade_mode():
    candidate = {
        "strategy_id": "S-BASE",
        "candidate_key": "S-BASE:short_only",
        "trade_mode": "short_only",
        "position_model": "single_side",
        "strategy_source": "graveyard",
        "strategy_stage": "archived",
        "strategy_name": "Base Short",
        "raw_metrics": {"total_trades": 12.0},
        "adjusted_metrics": {"total_trades": 12.0},
        "oos_raw_metrics": {"total_trades": 6.0},
        "oos_adjusted_metrics": {"total_trades": 6.0},
        "coverage": {"train_bars": 100, "oos_bars": 40},
        "diagnostics": {"train": {"status": "ok"}, "oos": {"status": "ok"}},
        "admission": {"admitted": True, "checks": {"trades_gte_75": True}},
        "score": 0.91,
        "score_components": {"return_norm": 1.0},
    }

    row = _candidate_to_score_row(
        candidate=candidate,
        regime="TREND_UP",
        regime_timeframe="1h",
        execution_timeframe="15m",
    )
    restored = _score_row_to_candidate(row)

    assert row["strategy_id"] == "S-BASE:short_only"
    assert dict(row["metrics_json"]["strategy_meta"])["strategy_id"] == "S-BASE"
    assert restored["strategy_id"] == "S-BASE"
    assert restored["candidate_key"] == "S-BASE:short_only"
    assert restored["trade_mode"] == "short_only"
    assert restored["position_model"] == "single_side"
