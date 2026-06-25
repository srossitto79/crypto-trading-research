from __future__ import annotations

import numpy as np
import pandas as pd

from axiom.lab_db import get_model_version, upsert_lab_experiment
from axiom.lab_models import ModelRebuildRequest
from axiom.lab_regime_engine import (
    apply_hysteresis,
    build_historical_snapshot,
    build_regime_segments,
    classify_features,
    compute_features_from_frame,
    run_model_rebuild,
    validate_no_lookahead_on_frame,
)


def _ohlcv_frame(periods: int = 420) -> pd.DataFrame:
    timestamps = pd.date_range("2025-01-01", periods=periods, freq="h", tz="UTC")
    base = np.linspace(100.0, 145.0, periods)
    oscillation = np.sin(np.linspace(0.0, 18.0, periods)) * 2.5
    close = base + oscillation
    open_ = close - 0.2
    high = close + 0.8
    low = close - 0.8
    volume = np.linspace(900.0, 1600.0, periods)
    return pd.DataFrame(
        {
            "timestamp": timestamps,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        }
    )


def test_hysteresis_requires_dwell_and_confidence_delta():
    timestamps = pd.date_range("2026-01-01", periods=22, freq="h", tz="UTC")
    raw_regimes = (
        ["TREND_UP_LOW_VOL"] * 8
        + ["RANGE_HIGH_VOL"] * 13
        + ["RANGE_HIGH_VOL"]
    )
    raw_confidences = [0.62] * 8 + [0.74] * 12 + [0.74] + [0.81]

    frame = pd.DataFrame(
        {
            "timestamp": timestamps,
            "raw_regime": raw_regimes,
            "raw_confidence": raw_confidences,
            "raw_meta": [{} for _ in raw_regimes],
        }
    )
    stabilized = apply_hysteresis(frame, min_dwell_bars=12, min_conf_delta=0.15)

    # No switch during first 12 candidate bars because confidence delta is below 0.15.
    assert stabilized.loc[20, "regime"] == "TREND_UP_LOW_VOL"
    # Switch only once both dwell and confidence delta constraints are met.
    assert stabilized.loc[21, "regime"] == "RANGE_HIGH_VOL"

    stabilized_again = apply_hysteresis(frame, min_dwell_bars=12, min_conf_delta=0.15)
    assert stabilized_again["regime"].tolist() == stabilized["regime"].tolist()
    assert stabilized_again["confidence"].tolist() == stabilized["confidence"].tolist()


def test_segment_builder_merges_short_fragment_by_stronger_neighbor_confidence():
    timestamps = pd.date_range("2026-02-01", periods=75, freq="h", tz="UTC")
    regimes = (
        ["RANGE_LOW_VOL"] * 30
        + ["TREND_UP_HIGH_VOL"] * 5
        + ["TREND_DOWN_LOW_VOL"] * 40
    )
    confidences = [0.55] * 30 + [0.90] * 5 + [0.60] * 40
    labels = pd.DataFrame(
        {
            "timestamp": timestamps,
            "regime": regimes,
            "confidence": confidences,
        }
    )

    segments = build_regime_segments(labels, min_segment_bars=24)
    assert len(segments) == 2
    assert segments.iloc[0]["regime"] == "RANGE_LOW_VOL"
    assert int(segments.iloc[0]["bars_count"]) == 30
    assert segments.iloc[1]["regime"] == "TREND_DOWN_LOW_VOL"
    assert int(segments.iloc[1]["bars_count"]) == 45

    segments_again = build_regime_segments(labels, min_segment_bars=24)
    assert segments_again.to_dict("records") == segments.to_dict("records")


def test_no_lookahead_validation_passes_on_walk_forward_replay():
    frame = _ohlcv_frame()

    validation = validate_no_lookahead_on_frame(frame, sample_bars=48)

    assert validation["passed"] is True
    assert validation["mismatch_count"] == 0
    assert validation["first_mismatch"] is None


def test_future_bars_do_not_repaint_existing_regime_labels():
    frame = _ohlcv_frame()
    full_classified = classify_features(compute_features_from_frame(frame))
    assert not full_classified.empty

    target_idx = len(full_classified) - 12
    target_ts = pd.Timestamp(full_classified.iloc[target_idx]["timestamp"])
    prefix_frame = frame[frame["timestamp"] <= target_ts].copy()
    replay_classified = classify_features(compute_features_from_frame(prefix_frame))

    full_row = full_classified.iloc[target_idx]
    replay_row = replay_classified.iloc[-1]

    assert pd.Timestamp(replay_row["timestamp"]) == target_ts
    assert replay_row["regime"] == full_row["regime"]
    assert replay_row["confidence"] == full_row["confidence"]


def test_gmm_classifier_is_prefix_invariant():
    frame = _ohlcv_frame(periods=420)
    classifier_config = {
        "n_components": 4,
        "window_bars": 240,
        "min_fit_bars": 120,
        "refit_interval": 24,
        "covariance_type": "diag",
        "n_init": 1,
        "max_iter": 40,
        "transition_probability": 0.35,
        "transition_entropy": 0.85,
    }

    full_classified = classify_features(
        compute_features_from_frame(frame),
        classifier_type="gmm_v1",
        classifier_config=classifier_config,
    )
    assert not full_classified.empty

    target_idx = len(full_classified) - 24
    target_ts = pd.Timestamp(full_classified.iloc[target_idx]["timestamp"])
    prefix_frame = frame[frame["timestamp"] <= target_ts].copy()
    replay_classified = classify_features(
        compute_features_from_frame(prefix_frame),
        classifier_type="gmm_v1",
        classifier_config=classifier_config,
    )

    full_row = full_classified.iloc[target_idx]
    replay_row = replay_classified.iloc[-1]

    assert pd.Timestamp(replay_row["timestamp"]) == target_ts
    assert replay_row["regime"] == full_row["regime"]
    assert replay_row["confidence"] == full_row["confidence"]
    assert replay_row["meta_json"]["classifier"]["type"] == "gmm_v1"


def test_snapshot_rebuilds_when_experiment_window_changes(monkeypatch, tmp_path):
    frame = _ohlcv_frame(periods=960)
    source_path = tmp_path / "source_snapshot.parquet"
    frame.to_parquet(source_path, index=False)

    monkeypatch.setattr("axiom.lab_regime_engine.parquet_path", lambda *_args, **_kwargs: source_path)
    monkeypatch.setattr("axiom.lab_regime_engine.compute_checksum", lambda *_args, **_kwargs: "checksum-test")
    monkeypatch.setattr("axiom.lab_regime_engine.load_parquet", lambda *_args, **_kwargs: frame.copy())

    experiment = upsert_lab_experiment(
        experiment_id="exp_snapshot_window",
        symbol="BTC/USDT",
        timeframe="1h",
        regime_timeframe="1h",
        execution_timeframe="15m",
        train_start="2025-01-01T00:00:00+00:00",
        train_end="2025-01-10T00:00:00+00:00",
        test_start="2025-01-10T00:00:01+00:00",
        test_end="2025-01-20T00:00:00+00:00",
        notes="Window one",
        status="queued",
    )
    manifest_one = build_historical_snapshot(experiment.id)

    upsert_lab_experiment(
        experiment_id=experiment.id,
        symbol="BTC/USDT",
        timeframe="1h",
        regime_timeframe="1h",
        execution_timeframe="15m",
        train_start="2025-01-01T00:00:00+00:00",
        train_end="2025-01-15T00:00:00+00:00",
        test_start="2025-01-15T00:00:01+00:00",
        test_end="2025-01-25T00:00:00+00:00",
        notes="Window two",
        status="queued",
    )
    manifest_two = build_historical_snapshot(experiment.id)

    assert manifest_two.snapshot_path != manifest_one.snapshot_path
    assert manifest_two.coverage_end != manifest_one.coverage_end


def test_model_rebuild_persists_classifier_metadata(monkeypatch, tmp_path):
    frame = _ohlcv_frame(periods=420)
    source_path = tmp_path / "source_snapshot.parquet"
    frame.to_parquet(source_path, index=False)

    monkeypatch.setattr("axiom.lab_regime_engine.parquet_path", lambda *_args, **_kwargs: source_path)
    monkeypatch.setattr("axiom.lab_regime_engine.compute_checksum", lambda *_args, **_kwargs: "checksum-gmm")
    monkeypatch.setattr("axiom.lab_regime_engine.load_parquet", lambda *_args, **_kwargs: frame.copy())

    experiment = upsert_lab_experiment(
        experiment_id="exp_gmm_rebuild",
        symbol="BTC/USDT",
        timeframe="1h",
        regime_timeframe="1h",
        execution_timeframe="15m",
        train_start="2025-01-01T00:00:00+00:00",
        train_end="2025-01-20T00:00:00+00:00",
        test_start="2025-01-20T00:00:01+00:00",
        test_end="2025-01-30T00:00:00+00:00",
        notes="GMM rebuild",
        status="queued",
    )

    response = run_model_rebuild(
        ModelRebuildRequest(
            experiment_id=experiment.id,
            classifier_type="gmm_v1",
            classifier_config={
                "n_components": 4,
                "window_bars": 240,
                "min_fit_bars": 120,
                "refit_interval": 24,
                "covariance_type": "diag",
                "n_init": 1,
                "max_iter": 40,
                "transition_probability": 0.35,
                "transition_entropy": 0.85,
                "validation_sample_bars": 8,
            },
        )
    )
    model_version = get_model_version(response.model_version_id)

    assert response.classifier_type == "gmm_v1"
    assert response.diagnostics["bars_classified"] > 0
    assert model_version is not None
    assert model_version.config_json["classifier"]["type"] == "gmm_v1"
    assert model_version.config_json["diagnostics"]["bars_classified"] == response.diagnostics["bars_classified"]
