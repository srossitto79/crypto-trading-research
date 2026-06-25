"""Phase 3 regime engine: snapshot wall, features, classifier, hysteresis, segmentation."""

from __future__ import annotations

import hashlib
import math
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import axiom.config as config
from axiom.data import compute_checksum, load_parquet, parquet_path
from axiom.lab_db import (
    create_or_update_model_version,
    get_lab_experiment,
    get_model_version,
    get_regime_labels,
    get_snapshot_manifest,
    replace_regime_labels,
    replace_regime_segments,
    update_lab_experiment_status,
    upsert_snapshot_manifest,
)
from axiom.lab_models import (
    ModelRebuildRequest,
    ModelRebuildResponse,
    SegmentBuildRequest,
    SegmentBuildResponse,
)
from axiom.scanner import adx, atr

REGIME_TAXONOMY: tuple[str, ...] = (
    "TREND_UP",
    "TREND_DOWN",
    "RANGE",
    "HIGH_VOL",
)
TRANSITION_OVERLAY = "TRANSITION"
LEGACY_REGIME_TO_CORE: dict[str, str] = {
    "TREND_UP": "TREND_UP",
    "TREND_UP_LOW_VOL": "TREND_UP",
    "TREND_DOWN": "TREND_DOWN",
    "TREND_DOWN_LOW_VOL": "TREND_DOWN",
    "RANGE": "RANGE",
    "RANGE_LOW_VOL": "RANGE",
    "HIGH_VOL": "HIGH_VOL",
    "TREND_UP_HIGH_VOL": "HIGH_VOL",
    "TREND_DOWN_HIGH_VOL": "HIGH_VOL",
    "RANGE_HIGH_VOL": "HIGH_VOL",
}
CORE_REGIME_QUERY_ALIASES: dict[str, tuple[str, ...]] = {
    "TREND_UP": ("TREND_UP", "TREND_UP_LOW_VOL"),
    "TREND_DOWN": ("TREND_DOWN", "TREND_DOWN_LOW_VOL"),
    "RANGE": ("RANGE", "RANGE_LOW_VOL"),
    "HIGH_VOL": ("HIGH_VOL", "TREND_UP_HIGH_VOL", "TREND_DOWN_HIGH_VOL", "RANGE_HIGH_VOL"),
}

MODEL_REBUILD_JOB_TYPE = "model_rebuild"
SEGMENT_BUILD_JOB_TYPE = "segments_build"

MIN_HYSTERESIS_DWELL_BARS = 12
MIN_HYSTERESIS_CONF_DELTA = 0.15
MIN_SEGMENT_BARS = 24
VOL_HIGH_THRESHOLD = 0.60
NO_LOOKAHEAD_VALIDATION_BARS = 96
NO_LOOKAHEAD_CONFIDENCE_TOLERANCE = 1e-9

LEGACY_RULE_CLASSIFIER = "legacy_rule"
GMM_V1_CLASSIFIER = "gmm_v1"
SUPPORTED_CLASSIFIER_TYPES = {
    LEGACY_RULE_CLASSIFIER,
    GMM_V1_CLASSIFIER,
}
DEFAULT_CLASSIFIER_TYPE = LEGACY_RULE_CLASSIFIER

DEFAULT_GMM_COMPONENTS = 6
DEFAULT_GMM_WINDOW_BARS = 2000
DEFAULT_GMM_MIN_FIT_BARS = 240
DEFAULT_GMM_REFIT_INTERVAL = 24
DEFAULT_GMM_RANDOM_STATE = 42
DEFAULT_GMM_COVARIANCE_TYPE = "diag"
DEFAULT_GMM_N_INIT = 1
DEFAULT_GMM_MAX_ITER = 200
DEFAULT_GMM_REG_COVAR = 1e-6
DEFAULT_GMM_TRANSITION_PROBABILITY = 0.42
DEFAULT_GMM_TRANSITION_ENTROPY = 0.72
DEFAULT_GMM_VALIDATION_SAMPLE_BARS = 24

CLASSIFIER_FEATURE_COLUMNS: tuple[str, ...] = (
    "ema_spread_pct",
    "ema_slope",
    "adx",
    "atr_percentile",
    "realized_vol_percentile",
    "range_efficiency",
)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _to_utc_timestamp(value: str | None) -> pd.Timestamp | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    parsed = pd.to_datetime(text, utc=True, errors="coerce")
    if pd.isna(parsed):
        return None
    return pd.Timestamp(parsed)


def _to_utc_iso(value: object) -> str:
    parsed = pd.to_datetime(value, utc=True, errors="coerce")
    if pd.isna(parsed):
        return ""
    return pd.Timestamp(parsed).isoformat().replace("+00:00", "Z")


def _sanitize_file_token(value: str) -> str:
    token = str(value or "").strip().upper()
    return token.replace("/", "-").replace("\\", "-").replace(":", "-").replace(" ", "_")


def experiment_regime_timeframe(experiment: Any) -> str:
    return str(
        getattr(experiment, "regime_timeframe", None)
        or getattr(experiment, "timeframe", None)
        or "1h"
    ).strip() or "1h"


def experiment_execution_timeframe(experiment: Any) -> str:
    return str(
        getattr(experiment, "execution_timeframe", None)
        or getattr(experiment, "regime_timeframe", None)
        or getattr(experiment, "timeframe", None)
        or "1h"
    ).strip() or "1h"


def _normalize_ohlcv_frame(frame: pd.DataFrame) -> pd.DataFrame:
    required = ["timestamp", "open", "high", "low", "close", "volume"]
    data = frame.copy()
    for column in required:
        if column not in data.columns:
            raise ValueError(f"OHLCV frame missing required column '{column}'")
    data["timestamp"] = pd.to_datetime(data["timestamp"], utc=True, errors="coerce")
    for column in ("open", "high", "low", "close", "volume"):
        data[column] = pd.to_numeric(data[column], errors="coerce")
    data = data.dropna(subset=required)
    data = data[required].sort_values("timestamp").drop_duplicates(subset=["timestamp"], keep="last")
    data = data.reset_index(drop=True)
    return data


def _resolve_experiment_window(
    *,
    frame: pd.DataFrame,
    train_start: str | None,
    train_end: str | None,
    test_start: str | None,
    test_end: str | None,
) -> tuple[pd.Timestamp, pd.Timestamp]:
    starts = [_to_utc_timestamp(train_start), _to_utc_timestamp(test_start)]
    ends = [_to_utc_timestamp(train_end), _to_utc_timestamp(test_end)]
    starts = [value for value in starts if value is not None]
    ends = [value for value in ends if value is not None]

    dataset_start = pd.Timestamp(frame["timestamp"].iloc[0])
    dataset_end = pd.Timestamp(frame["timestamp"].iloc[-1])

    if starts:
        start = min(starts)
    else:
        start = max(dataset_start, dataset_end - timedelta(days=365))

    if ends:
        end = max(ends)
    else:
        end = dataset_end

    if start < dataset_start:
        start = dataset_start
    if end > dataset_end:
        end = dataset_end
    if end < start:
        raise ValueError("Experiment window resolves to an invalid time range")
    return start, end


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_obj:
        for chunk in iter(lambda: file_obj.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_snapshot_parquet(path: Path, frame: pd.DataFrame, metadata: dict[str, str]) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    table = pa.Table.from_pandas(frame, preserve_index=False)
    schema_meta = dict(table.schema.metadata or {})
    for key, value in metadata.items():
        schema_meta[str(key).encode("utf-8")] = str(value).encode("utf-8")
    table = table.replace_schema_metadata(schema_meta)
    tmp_path = Path(str(path) + ".tmp")
    pq.write_table(table, tmp_path, compression="zstd")
    os.replace(str(tmp_path), str(path))


def _rolling_last_percentile(series: pd.Series, window: int) -> pd.Series:
    safe_window = max(2, int(window))

    def _percentile(values: np.ndarray) -> float:
        if values.size == 0:
            return float("nan")
        finite = values[np.isfinite(values)]
        if finite.size == 0:
            return float("nan")
        last = values[-1]
        if not np.isfinite(last):
            return float("nan")
        return float((finite <= last).sum() / finite.size)

    return series.rolling(window=safe_window, min_periods=max(5, safe_window // 8)).apply(
        _percentile,
        raw=True,
    )


def normalize_core_regime(
    value: object,
    *,
    allow_transition_overlay: bool = False,
) -> str | None:
    normalized = str(value or "").strip().upper()
    if not normalized:
        return None
    if allow_transition_overlay and normalized == TRANSITION_OVERLAY:
        return TRANSITION_OVERLAY
    return LEGACY_REGIME_TO_CORE.get(normalized)


def regime_query_aliases(value: object) -> tuple[str, ...]:
    normalized = str(value or "").strip().upper()
    if not normalized:
        return tuple()
    core_regime = normalize_core_regime(normalized)
    if core_regime is None:
        if normalized == TRANSITION_OVERLAY:
            return (TRANSITION_OVERLAY,)
        return (normalized,)
    return CORE_REGIME_QUERY_ALIASES.get(core_regime, (core_regime,))


def _resolve_transition_core_regime(
    row: pd.Series,
    *,
    trend_state: str,
    vol_state: str,
    vol_combined: float,
    structure_state: str,
) -> str:
    if vol_state == "high" and vol_combined >= VOL_HIGH_THRESHOLD:
        return "HIGH_VOL"
    if trend_state == "up":
        return "TREND_UP"
    if trend_state == "down":
        return "TREND_DOWN"
    if trend_state == "range" or structure_state == "range":
        return "RANGE"
    alignment = int(row.get("ema_alignment", 0) or 0)
    if alignment > 0:
        return "TREND_UP"
    if alignment < 0:
        return "TREND_DOWN"
    range_eff = float(row.get("range_efficiency", 0.5) or 0.5)
    if range_eff <= 0.50:
        return "RANGE"
    return "HIGH_VOL" if vol_state == "high" else "RANGE"


def _trend_component(row: pd.Series) -> tuple[str, float]:
    alignment = int(row["ema_alignment"])
    slope = float(row["ema_slope"])
    adx_value = float(row["adx"])
    range_eff = float(row["range_efficiency"])
    slope_abs_score = min(1.0, abs(slope) * 600.0)
    adx_score = min(1.0, max(0.0, adx_value) / 35.0)

    if alignment > 0 and slope > 0:
        confidence = min(1.0, 0.50 * adx_score + 0.35 * slope_abs_score + 0.15)
        return "up", confidence
    if alignment < 0 and slope < 0:
        confidence = min(1.0, 0.50 * adx_score + 0.35 * slope_abs_score + 0.15)
        return "down", confidence
    if adx_value < 18.0 or range_eff < 0.45:
        range_conf = min(1.0, 0.60 * (1.0 - min(1.0, adx_value / 25.0)) + 0.40 * (1.0 - range_eff))
        return "range", range_conf
    transition_conf = min(1.0, 0.60 * (1.0 - adx_score) + 0.40 * (1.0 - slope_abs_score))
    return "transition", transition_conf


def _structure_component(row: pd.Series) -> tuple[str, float]:
    range_eff = float(row["range_efficiency"])
    if range_eff >= 0.62:
        return "trend", min(1.0, (range_eff - 0.50) / 0.50)
    if range_eff <= 0.42:
        return "range", min(1.0, (0.50 - range_eff) / 0.50)
    return "transition", min(1.0, 1.0 - abs(range_eff - 0.50) * 4.0)


def _vol_component(row: pd.Series) -> tuple[str, float, float]:
    atr_percentile = float(row["atr_percentile"])
    rv_percentile = float(row["realized_vol_percentile"])
    combined = float(np.nanmean([atr_percentile, rv_percentile]))
    if math.isnan(combined):
        combined = 0.50
    state = "high" if combined >= VOL_HIGH_THRESHOLD else "low"
    confidence = min(1.0, abs(combined - 0.50) / 0.50)
    return state, confidence, combined


def _classify_bar(row: pd.Series) -> tuple[str, float, dict[str, Any]]:
    trend_state, trend_conf = _trend_component(row)
    vol_state, vol_conf, vol_combined = _vol_component(row)
    structure_state, structure_conf = _structure_component(row)

    if trend_state == "up" and trend_conf >= 0.55 and structure_state != "range":
        legacy_label = "TREND_UP_HIGH_VOL" if vol_state == "high" else "TREND_UP_LOW_VOL"
        confidence = min(
            1.0,
            0.50 * trend_conf + 0.30 * (structure_conf if structure_state == "trend" else 0.0) + 0.20 * vol_conf,
        )
    elif trend_state == "down" and trend_conf >= 0.55 and structure_state != "range":
        legacy_label = "TREND_DOWN_HIGH_VOL" if vol_state == "high" else "TREND_DOWN_LOW_VOL"
        confidence = min(
            1.0,
            0.50 * trend_conf + 0.30 * (structure_conf if structure_state == "trend" else 0.0) + 0.20 * vol_conf,
        )
    elif trend_state == "range" and trend_conf >= 0.50 and structure_state != "trend":
        legacy_label = "RANGE_HIGH_VOL" if vol_state == "high" else "RANGE_LOW_VOL"
        confidence = min(
            1.0,
            0.50 * trend_conf + 0.30 * (structure_conf if structure_state == "range" else 0.0) + 0.20 * vol_conf,
        )
    else:
        legacy_label = TRANSITION_OVERLAY
        confidence = min(
            1.0,
            0.45 * (1.0 - trend_conf) + 0.35 * (1.0 - structure_conf) + 0.20 * (1.0 - vol_conf),
        )

    label = normalize_core_regime(legacy_label)
    uncertain = legacy_label == TRANSITION_OVERLAY
    if label is None:
        label = _resolve_transition_core_regime(
            row,
            trend_state=trend_state,
            vol_state=vol_state,
            vol_combined=vol_combined,
            structure_state=structure_state,
        )
        uncertain = True

    meta = {
        "core_regime": label,
        "legacy_label": legacy_label,
        "trend_state": trend_state,
        "trend_confidence": round(trend_conf, 6),
        "vol_state": vol_state,
        "vol_confidence": round(vol_conf, 6),
        "vol_combined": round(vol_combined, 6),
        "structure_state": structure_state,
        "structure_confidence": round(structure_conf, 6),
        "uncertain": uncertain,
        "overlay_regime": (TRANSITION_OVERLAY if uncertain else None),
    }
    return label, float(confidence), meta


def build_historical_snapshot(experiment_id: str):
    experiment = get_lab_experiment(experiment_id)
    if experiment is None:
        raise ValueError(f"Unknown experiment: {experiment_id}")

    regime_timeframe = experiment_regime_timeframe(experiment)
    execution_timeframe = experiment_execution_timeframe(experiment)
    source_dataset_path = parquet_path(experiment.symbol, regime_timeframe)
    source_dataset_checksum = compute_checksum(experiment.symbol, regime_timeframe)
    raw = load_parquet(experiment.symbol, regime_timeframe)
    if raw is None or raw.empty:
        raise ValueError(
            f"No OHLCV dataset found for experiment {experiment_id} ({experiment.symbol} {regime_timeframe})"
        )
    frame = _normalize_ohlcv_frame(raw)
    start, end = _resolve_experiment_window(
        frame=frame,
        train_start=experiment.train_start,
        train_end=experiment.train_end,
        test_start=experiment.test_start,
        test_end=experiment.test_end,
    )
    requested_window_start = _to_utc_iso(start)
    requested_window_end = _to_utc_iso(end)

    existing_manifest = get_snapshot_manifest(experiment_id)
    if existing_manifest is not None and Path(existing_manifest.snapshot_path).exists():
        manifest_json = dict(existing_manifest.manifest_json or {})
        existing_window_start = str(
            manifest_json.get("requested_window_start") or existing_manifest.coverage_start or ""
        ).strip()
        existing_window_end = str(
            manifest_json.get("requested_window_end") or existing_manifest.coverage_end or ""
        ).strip()
        existing_checksum = str(manifest_json.get("source_dataset_checksum") or "").strip()
        if (
            existing_window_start == requested_window_start
            and existing_window_end == requested_window_end
            and existing_checksum == str(source_dataset_checksum or "").strip()
            and str(existing_manifest.symbol or "") == str(experiment.symbol or "")
            and str(existing_manifest.timeframe or "") == str(regime_timeframe or "")
        ):
            return existing_manifest

    clipped = frame[(frame["timestamp"] >= start) & (frame["timestamp"] <= end)].copy()
    if clipped.empty:
        raise ValueError(
            f"Experiment {experiment_id} window has no rows for {experiment.symbol} {regime_timeframe}"
        )

    snapshot_dir = config.AXIOM_HOME / "lab" / "snapshots"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    provisional = snapshot_dir / (
        f"{experiment.id}_{_sanitize_file_token(experiment.symbol)}_{regime_timeframe}_{_now_iso().replace(':', '-')}.parquet"
    )
    metadata = {
        "lab_experiment_id": experiment.id,
        "lab_symbol": experiment.symbol,
        "lab_timeframe": regime_timeframe,
        "lab_created_at": _now_iso(),
    }
    _write_snapshot_parquet(provisional, clipped, metadata)
    snapshot_hash = _sha256_file(provisional)
    final_path = snapshot_dir / (
        f"{experiment.id}_{_sanitize_file_token(experiment.symbol)}_{regime_timeframe}_{snapshot_hash[:16]}.parquet"
    )
    if final_path.exists():
        provisional.unlink(missing_ok=True)
    else:
        os.replace(str(provisional), str(final_path))
    try:
        final_path.chmod(0o444)
    except Exception:
        # Best-effort on Windows and environments that ignore chmod bits.
        pass

    manifest = upsert_snapshot_manifest(
        experiment_id=experiment.id,
        snapshot_path=str(final_path),
        snapshot_hash=snapshot_hash,
        symbol=experiment.symbol,
        timeframe=regime_timeframe,
        row_count=int(len(clipped)),
        coverage_start=_to_utc_iso(clipped["timestamp"].iloc[0]),
        coverage_end=_to_utc_iso(clipped["timestamp"].iloc[-1]),
        manifest_json={
            "requested_window_start": requested_window_start,
            "requested_window_end": requested_window_end,
            "snapshot_row_count": int(len(clipped)),
            "source_dataset_row_count": int(len(frame)),
            "source_kind": "parquet_dataset",
            "source_dataset_path": str(source_dataset_path),
            "source_dataset_checksum": source_dataset_checksum,
            "regime_timeframe": regime_timeframe,
            "execution_timeframe": execution_timeframe,
        },
    )
    update_lab_experiment_status(experiment.id, "snapshot_ready")
    return manifest


def compute_features_from_snapshot(snapshot_path: str) -> pd.DataFrame:
    frame = pd.read_parquet(snapshot_path)
    return compute_features_from_frame(frame)


def compute_features_from_frame(frame: pd.DataFrame) -> pd.DataFrame:
    data = _normalize_ohlcv_frame(frame)
    close = data["close"]

    data["ema_fast"] = close.ewm(span=20, adjust=False).mean()
    data["ema_mid"] = close.ewm(span=50, adjust=False).mean()
    data["ema_slow"] = close.ewm(span=200, adjust=False).mean()
    data["ema_spread_pct"] = ((data["ema_fast"] - data["ema_slow"]) / close.clip(lower=1e-9)).fillna(0.0)
    data["ema_alignment"] = np.select(
        [
            (data["ema_fast"] > data["ema_mid"]) & (data["ema_mid"] > data["ema_slow"]),
            (data["ema_fast"] < data["ema_mid"]) & (data["ema_mid"] < data["ema_slow"]),
        ],
        [1, -1],
        default=0,
    ).astype(int)
    data["ema_slope"] = data["ema_mid"].pct_change(5).fillna(0.0)
    data["adx"] = adx(data, period=14)
    data["atr"] = atr(data, period=14)
    data["atr_percentile"] = _rolling_last_percentile(data["atr"], window=252)
    log_returns = np.log(close.clip(lower=1e-9)).diff()
    data["realized_vol"] = log_returns.rolling(20, min_periods=5).std() * np.sqrt(20.0)
    data["realized_vol_percentile"] = _rolling_last_percentile(data["realized_vol"], window=252)

    lookback = 14
    displacement = close.diff(lookback).abs()
    path = close.diff().abs().rolling(lookback, min_periods=lookback).sum()
    data["range_efficiency"] = (displacement / path.clip(lower=1e-9)).clip(lower=0.0, upper=1.0)
    data["choppiness"] = (1.0 - data["range_efficiency"]).clip(lower=0.0, upper=1.0)

    data = data.dropna(
        subset=[
            "ema_fast",
            "ema_mid",
            "ema_slow",
            "ema_spread_pct",
            "adx",
            "atr_percentile",
            "realized_vol_percentile",
            "range_efficiency",
            "choppiness",
        ]
    ).reset_index(drop=True)
    return data


def normalize_classifier_type(value: object) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in SUPPORTED_CLASSIFIER_TYPES:
        return normalized
    return DEFAULT_CLASSIFIER_TYPE


def _coerce_int(value: object, default: int, *, minimum: int) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = int(default)
    return max(int(minimum), parsed)


def _coerce_float(value: object, default: float, *, minimum: float, maximum: float | None = None) -> float:
    try:
        parsed = float(value)
    except Exception:
        parsed = float(default)
    parsed = max(float(minimum), parsed)
    if maximum is not None:
        parsed = min(float(maximum), parsed)
    return float(parsed)


def resolve_classifier_config(
    classifier_type: str,
    classifier_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized = normalize_classifier_type(classifier_type)
    if normalized != GMM_V1_CLASSIFIER:
        return {}

    raw = dict(classifier_config or {})
    requested_features = raw.get("feature_columns")
    if isinstance(requested_features, (list, tuple, set)):
        feature_columns = [
            str(column).strip()
            for column in requested_features
            if str(column).strip() in CLASSIFIER_FEATURE_COLUMNS
        ]
    else:
        feature_columns = []
    if not feature_columns:
        feature_columns = list(CLASSIFIER_FEATURE_COLUMNS)

    return {
        "n_components": _coerce_int(raw.get("n_components"), DEFAULT_GMM_COMPONENTS, minimum=2),
        "window_bars": _coerce_int(raw.get("window_bars"), DEFAULT_GMM_WINDOW_BARS, minimum=120),
        "min_fit_bars": _coerce_int(raw.get("min_fit_bars"), DEFAULT_GMM_MIN_FIT_BARS, minimum=48),
        "refit_interval": _coerce_int(raw.get("refit_interval"), DEFAULT_GMM_REFIT_INTERVAL, minimum=1),
        "random_state": _coerce_int(raw.get("random_state"), DEFAULT_GMM_RANDOM_STATE, minimum=0),
        "covariance_type": str(raw.get("covariance_type") or DEFAULT_GMM_COVARIANCE_TYPE).strip() or DEFAULT_GMM_COVARIANCE_TYPE,
        "n_init": _coerce_int(raw.get("n_init"), DEFAULT_GMM_N_INIT, minimum=1),
        "max_iter": _coerce_int(raw.get("max_iter"), DEFAULT_GMM_MAX_ITER, minimum=10),
        "reg_covar": _coerce_float(raw.get("reg_covar"), DEFAULT_GMM_REG_COVAR, minimum=1e-9),
        "transition_probability": _coerce_float(
            raw.get("transition_probability"),
            DEFAULT_GMM_TRANSITION_PROBABILITY,
            minimum=0.0,
            maximum=1.0,
        ),
        "transition_entropy": _coerce_float(
            raw.get("transition_entropy"),
            DEFAULT_GMM_TRANSITION_ENTROPY,
            minimum=0.0,
            maximum=1.0,
        ),
        "validation_sample_bars": _coerce_int(
            raw.get("validation_sample_bars"),
            DEFAULT_GMM_VALIDATION_SAMPLE_BARS,
            minimum=4,
        ),
        "feature_columns": list(feature_columns),
    }


def _empty_classified_frame() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "timestamp",
            "raw_regime",
            "raw_confidence",
            "raw_meta",
            "confidence",
            "regime",
            "meta_json",
        ]
    )


def _finalize_classified_frame(
    classified: pd.DataFrame,
    *,
    classifier_type: str,
    classifier_config: dict[str, Any] | None = None,
) -> pd.DataFrame:
    if classified.empty:
        return _empty_classified_frame()

    normalized_classifier = normalize_classifier_type(classifier_type)
    resolved_config = resolve_classifier_config(normalized_classifier, classifier_config)
    stabilized = apply_hysteresis(
        classified,
        min_dwell_bars=MIN_HYSTERESIS_DWELL_BARS,
        min_conf_delta=MIN_HYSTERESIS_CONF_DELTA,
    )
    stabilized["meta_json"] = stabilized.apply(
        lambda current: {
            "raw_regime": str(current["raw_regime"]),
            "raw_confidence": float(current["raw_confidence"]),
            "hysteresis": {
                "min_dwell_bars": MIN_HYSTERESIS_DWELL_BARS,
                "min_conf_delta": MIN_HYSTERESIS_CONF_DELTA,
            },
            "classifier": {
                "type": normalized_classifier,
                "config": dict(resolved_config),
            },
            "components": dict(current["raw_meta"] or {}),
        },
        axis=1,
    )
    return stabilized


def _classify_features_rule_based(feature_frame: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for _, row in feature_frame.iterrows():
        regime, confidence, meta = _classify_bar(row)
        rows.append(
            {
                "timestamp": pd.Timestamp(row["timestamp"]),
                "raw_regime": regime,
                "raw_confidence": float(confidence),
                "raw_meta": meta,
            }
        )
    return pd.DataFrame(rows)


def _normalized_entropy(probabilities: np.ndarray) -> float:
    safe = np.asarray(probabilities, dtype=float)
    safe = safe[safe > 0.0]
    if safe.size <= 1:
        return 0.0
    entropy = -float(np.sum(safe * np.log(safe)))
    return float(entropy / math.log(float(len(probabilities))))


def _infer_component_regime(center: dict[str, float]) -> tuple[str, dict[str, float]]:
    trend_signal = float(
        0.60 * np.tanh(float(center.get("ema_spread_pct", 0.0)) * 80.0)
        + 0.40 * np.tanh(float(center.get("ema_slope", 0.0)) * 350.0)
    )
    adx_score = min(1.0, max(0.0, float(center.get("adx", 0.0))) / 35.0)
    range_eff = float(center.get("range_efficiency", 0.5))
    range_score = float(
        0.55 * (1.0 - min(1.0, max(0.0, range_eff)))
        + 0.25 * (1.0 - adx_score)
        + 0.20 * (1.0 - min(1.0, abs(trend_signal)))
    )
    trend_strength = float(0.65 * min(1.0, abs(trend_signal)) + 0.35 * adx_score)
    vol_score = float(
        np.nanmean(
            [
                float(center.get("atr_percentile", 0.5)),
                float(center.get("realized_vol_percentile", 0.5)),
            ]
        )
    )
    if math.isnan(vol_score):
        vol_score = 0.5

    if vol_score >= VOL_HIGH_THRESHOLD:
        label = "HIGH_VOL"
    elif trend_strength >= max(0.45, range_score + 0.08):
        base_regime = "TREND_UP" if trend_signal >= 0.0 else "TREND_DOWN"
        label = base_regime
    elif range_score >= 0.40:
        label = "RANGE"
    else:
        label = "RANGE" if abs(trend_signal) < 0.18 else ("TREND_UP" if trend_signal >= 0.0 else "TREND_DOWN")
    return label, {
        "trend_signal": round(trend_signal, 6),
        "trend_strength": round(trend_strength, 6),
        "range_score": round(range_score, 6),
        "vol_score": round(vol_score, 6),
    }


def _fit_gmm_window(
    feature_frame: pd.DataFrame,
    classifier_config: dict[str, Any],
) -> dict[str, Any]:
    from sklearn.mixture import GaussianMixture

    feature_columns = list(classifier_config.get("feature_columns") or CLASSIFIER_FEATURE_COLUMNS)
    matrix = np.nan_to_num(feature_frame[feature_columns].to_numpy(dtype=float), nan=0.0, posinf=0.0, neginf=0.0)
    if matrix.ndim != 2 or matrix.shape[0] < 2:
        raise ValueError("Insufficient rows to fit GMM classifier")

    mean = matrix.mean(axis=0)
    std = matrix.std(axis=0)
    std = np.where(std < 1e-6, 1.0, std)
    scaled = (matrix - mean) / std

    n_components = max(2, min(int(classifier_config["n_components"]), int(matrix.shape[0])))
    import warnings
    model = GaussianMixture(
        n_components=n_components,
        covariance_type=str(classifier_config["covariance_type"]),
        n_init=int(classifier_config["n_init"]),
        max_iter=int(classifier_config["max_iter"]),
        reg_covar=float(classifier_config["reg_covar"]),
        random_state=int(classifier_config["random_state"]),
    )
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=FutureWarning)
        warnings.filterwarnings("ignore", message=".*did not converge.*")
        model.fit(scaled)

    component_meta: dict[int, dict[str, Any]] = {}
    component_mapping: dict[int, str] = {}
    restored_means = (model.means_ * std) + mean
    for component_idx, center_values in enumerate(restored_means):
        center = {
            column: float(center_values[col_idx])
            for col_idx, column in enumerate(feature_columns)
        }
        label, diagnostics = _infer_component_regime(center)
        component_mapping[component_idx] = label
        component_meta[component_idx] = {
            "label": label,
            "center": {key: round(value, 6) for key, value in center.items()},
            "diagnostics": diagnostics,
        }

    return {
        "feature_columns": feature_columns,
        "mean": mean,
        "std": std,
        "model": model,
        "component_mapping": component_mapping,
        "component_meta": component_meta,
    }


def _classify_features_gmm(
    feature_frame: pd.DataFrame,
    *,
    classifier_config: dict[str, Any] | None = None,
) -> pd.DataFrame:
    resolved_config = resolve_classifier_config(GMM_V1_CLASSIFIER, classifier_config)
    safe_window_bars = int(resolved_config["window_bars"])
    safe_min_fit_bars = max(int(resolved_config["min_fit_bars"]), int(resolved_config["n_components"]) * 8)
    safe_refit_interval = int(resolved_config["refit_interval"])

    rows: list[dict[str, Any]] = []
    active_fit: dict[str, Any] | None = None
    last_fit_idx = -safe_refit_interval

    for idx, row in feature_frame.iterrows():
        if (idx + 1) < safe_min_fit_bars:
            regime, confidence, meta = _classify_bar(row)
            rows.append(
                {
                    "timestamp": pd.Timestamp(row["timestamp"]),
                    "raw_regime": regime,
                    "raw_confidence": float(confidence),
                    "raw_meta": {
                        **meta,
                        "classifier_type": GMM_V1_CLASSIFIER,
                        "mode": "legacy_warmup",
                    },
                }
            )
            continue

        window_start = max(0, (idx + 1) - safe_window_bars)
        window_frame = feature_frame.iloc[window_start : idx + 1].copy()
        if active_fit is None or (idx - last_fit_idx) >= safe_refit_interval:
            try:
                active_fit = _fit_gmm_window(window_frame, resolved_config)
                last_fit_idx = int(idx)
            except Exception as exc:
                regime, confidence, meta = _classify_bar(row)
                rows.append(
                    {
                        "timestamp": pd.Timestamp(row["timestamp"]),
                        "raw_regime": regime,
                        "raw_confidence": float(confidence),
                        "raw_meta": {
                            **meta,
                            "classifier_type": GMM_V1_CLASSIFIER,
                            "mode": "legacy_fallback",
                            "error": str(exc),
                        },
                    }
                )
                active_fit = None
                continue

        feature_columns = list(active_fit["feature_columns"])
        point = np.nan_to_num(window_frame[feature_columns].tail(1).to_numpy(dtype=float), nan=0.0, posinf=0.0, neginf=0.0)
        scaled = (point - active_fit["mean"]) / active_fit["std"]
        probabilities = np.asarray(active_fit["model"].predict_proba(scaled)[0], dtype=float)
        component_id = int(np.argmax(probabilities))
        posterior_max = float(probabilities[component_id])
        posterior_second = float(np.partition(probabilities, -2)[-2]) if probabilities.size > 1 else 0.0
        posterior_entropy = _normalized_entropy(probabilities)

        mapped_regime = str(active_fit["component_mapping"].get(component_id) or "RANGE")
        raw_regime = mapped_regime
        uncertain = False
        if (
            posterior_max < float(resolved_config["transition_probability"])
            or posterior_entropy > float(resolved_config["transition_entropy"])
        ):
            uncertain = True

        component_meta = dict(active_fit["component_meta"].get(component_id) or {})
        rows.append(
            {
                "timestamp": pd.Timestamp(row["timestamp"]),
                "raw_regime": raw_regime,
                "raw_confidence": posterior_max,
                "raw_meta": {
                    "classifier_type": GMM_V1_CLASSIFIER,
                    "mode": "gmm",
                    "component_id": component_id,
                    "mapped_regime": mapped_regime,
                    "posterior_max": round(posterior_max, 6),
                    "posterior_second": round(posterior_second, 6),
                    "posterior_entropy": round(posterior_entropy, 6),
                    "uncertain": uncertain,
                    "overlay_regime": (TRANSITION_OVERLAY if uncertain else None),
                    "fit_bar_count": int(len(window_frame)),
                    "window_start": _to_utc_iso(window_frame["timestamp"].iloc[0]),
                    "window_end": _to_utc_iso(window_frame["timestamp"].iloc[-1]),
                    "component": component_meta,
                },
            }
        )

    return pd.DataFrame(rows)


def classify_features(
    feature_frame: pd.DataFrame,
    *,
    classifier_type: str = DEFAULT_CLASSIFIER_TYPE,
    classifier_config: dict[str, Any] | None = None,
) -> pd.DataFrame:
    if feature_frame.empty:
        return _empty_classified_frame()

    normalized_classifier = normalize_classifier_type(classifier_type)
    if normalized_classifier == GMM_V1_CLASSIFIER:
        classified = _classify_features_gmm(feature_frame, classifier_config=classifier_config)
    else:
        classified = _classify_features_rule_based(feature_frame)
    return _finalize_classified_frame(
        classified,
        classifier_type=normalized_classifier,
        classifier_config=classifier_config,
    )


def summarize_classification_diagnostics(classified_frame: pd.DataFrame) -> dict[str, Any]:
    if classified_frame.empty:
        return {
            "bars_classified": 0,
            "label_distribution": {},
            "raw_label_distribution": {},
            "uncertain_share": 0.0,
            "raw_uncertain_share": 0.0,
            "transition_share": 0.0,
            "raw_transition_share": 0.0,
            "segment_count": 0,
            "median_segment_bars": 0.0,
            "confidence_mean": 0.0,
            "confidence_p25": 0.0,
            "confidence_p75": 0.0,
            "classification_modes": {},
        }

    total_rows = int(len(classified_frame))
    label_distribution = {
        str(label): int(count)
        for label, count in classified_frame["regime"].value_counts(dropna=False).sort_index().items()
    }
    raw_label_distribution = {
        str(label): int(count)
        for label, count in classified_frame["raw_regime"].value_counts(dropna=False).sort_index().items()
    }
    mode_distribution: dict[str, int] = {}
    for meta in classified_frame["raw_meta"].tolist():
        mode = str((meta or {}).get("mode") or "unknown")
        mode_distribution[mode] = int(mode_distribution.get(mode, 0) + 1)
    raw_uncertain_flags = [
        bool((meta or {}).get("uncertain"))
        or str((meta or {}).get("overlay_regime") or "").strip().upper() == TRANSITION_OVERLAY
        for meta in classified_frame["raw_meta"].tolist()
    ]
    resolved_uncertain_flags = [
        bool(dict((meta or {}).get("components") or {}).get("uncertain"))
        or str(dict((meta or {}).get("components") or {}).get("overlay_regime") or "").strip().upper() == TRANSITION_OVERLAY
        for meta in classified_frame["meta_json"].tolist()
    ]

    segments = _segment_boundaries(
        [str(value) for value in classified_frame["regime"].tolist()],
        [float(value) for value in classified_frame["confidence"].tolist()],
    )
    segment_lengths = [int(segment["bars_count"]) for segment in segments]
    confidence_values = classified_frame["confidence"].astype(float)

    return {
        "bars_classified": total_rows,
        "label_distribution": label_distribution,
        "raw_label_distribution": raw_label_distribution,
        "uncertain_share": round(float(sum(resolved_uncertain_flags) / total_rows), 6),
        "raw_uncertain_share": round(float(sum(raw_uncertain_flags) / total_rows), 6),
        "transition_share": round(float(sum(resolved_uncertain_flags) / total_rows), 6),
        "raw_transition_share": round(float(sum(raw_uncertain_flags) / total_rows), 6),
        "segment_count": int(len(segments)),
        "median_segment_bars": float(np.median(segment_lengths)) if segment_lengths else 0.0,
        "confidence_mean": round(float(confidence_values.mean()), 6),
        "confidence_p25": round(float(confidence_values.quantile(0.25)), 6),
        "confidence_p75": round(float(confidence_values.quantile(0.75)), 6),
        "classification_modes": mode_distribution,
    }


def validate_no_lookahead_on_frame(
    raw_frame: pd.DataFrame,
    *,
    sample_bars: int = NO_LOOKAHEAD_VALIDATION_BARS,
    confidence_tolerance: float = NO_LOOKAHEAD_CONFIDENCE_TOLERANCE,
    classifier_type: str = DEFAULT_CLASSIFIER_TYPE,
    classifier_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized = _normalize_ohlcv_frame(raw_frame)
    full_features = compute_features_from_frame(normalized)
    full_classified = classify_features(
        full_features,
        classifier_type=classifier_type,
        classifier_config=classifier_config,
    )
    if full_classified.empty:
        return {
            "passed": False,
            "status": "insufficient_history",
            "compared_bars": 0,
            "mismatch_count": 0,
            "sample_bars": 0,
        }

    safe_sample = max(1, min(int(sample_bars), len(full_classified)))
    compare_rows = full_classified.tail(safe_sample).reset_index(drop=True)
    mismatches: list[dict[str, Any]] = []

    for _, full_row in compare_rows.iterrows():
        target_ts = pd.Timestamp(full_row["timestamp"])
        prefix_raw = normalized[normalized["timestamp"] <= target_ts].copy()
        prefix_features = compute_features_from_frame(prefix_raw)
        prefix_classified = classify_features(
            prefix_features,
            classifier_type=classifier_type,
            classifier_config=classifier_config,
        )
        if prefix_classified.empty:
            mismatches.append(
                {
                    "timestamp": _to_utc_iso(target_ts),
                    "reason": "prefix_empty",
                }
            )
            continue
        replay_row = prefix_classified.iloc[-1]
        same_regime = str(replay_row["regime"]) == str(full_row["regime"])
        same_confidence = abs(float(replay_row["confidence"]) - float(full_row["confidence"])) <= confidence_tolerance
        if same_regime and same_confidence:
            continue
        mismatches.append(
            {
                "timestamp": _to_utc_iso(target_ts),
                "full_regime": str(full_row["regime"]),
                "replay_regime": str(replay_row["regime"]),
                "full_confidence": float(full_row["confidence"]),
                "replay_confidence": float(replay_row["confidence"]),
                "reason": "label_mismatch",
            }
        )

    return {
        "passed": len(mismatches) == 0,
        "status": "ok" if not mismatches else "mismatch",
        "compared_bars": int(len(compare_rows)),
        "mismatch_count": int(len(mismatches)),
        "sample_bars": int(safe_sample),
        "confidence_tolerance": float(confidence_tolerance),
        "classifier_type": normalize_classifier_type(classifier_type),
        "first_mismatch": (mismatches[0] if mismatches else None),
    }


def apply_hysteresis(
    classified_frame: pd.DataFrame,
    *,
    min_dwell_bars: int = MIN_HYSTERESIS_DWELL_BARS,
    min_conf_delta: float = MIN_HYSTERESIS_CONF_DELTA,
) -> pd.DataFrame:
    if classified_frame.empty:
        return classified_frame.copy()

    safe_dwell = max(1, int(min_dwell_bars))
    safe_delta = max(0.0, float(min_conf_delta))
    frame = classified_frame.sort_values("timestamp").reset_index(drop=True).copy()

    current_regime = str(frame.loc[0, "raw_regime"])
    current_conf = float(frame.loc[0, "raw_confidence"])
    pending_regime: str | None = None
    pending_count = 0

    resolved_regimes: list[str] = []
    resolved_confidences: list[float] = []

    for _, row in frame.iterrows():
        candidate_regime = str(row["raw_regime"])
        candidate_conf = float(row["raw_confidence"])

        if candidate_regime == current_regime:
            current_conf = candidate_conf
            pending_regime = None
            pending_count = 0
        else:
            if pending_regime == candidate_regime:
                pending_count += 1
            else:
                pending_regime = candidate_regime
                pending_count = 1

            if pending_count >= safe_dwell and (candidate_conf - current_conf) >= safe_delta:
                current_regime = candidate_regime
                current_conf = candidate_conf
                pending_regime = None
                pending_count = 0

        resolved_regimes.append(current_regime)
        resolved_confidences.append(candidate_conf if current_regime == candidate_regime else current_conf)

    frame["regime"] = resolved_regimes
    frame["confidence"] = resolved_confidences
    return frame


def _segment_boundaries(labels: list[str], confidences: list[float]) -> list[dict[str, Any]]:
    if not labels:
        return []
    segments: list[dict[str, Any]] = []
    start_idx = 0
    for idx in range(1, len(labels) + 1):
        boundary = idx == len(labels) or labels[idx] != labels[start_idx]
        if not boundary:
            continue
        end_idx = idx - 1
        segment_conf = float(np.mean(confidences[start_idx : end_idx + 1])) if end_idx >= start_idx else 0.0
        segments.append(
            {
                "start_idx": start_idx,
                "end_idx": end_idx,
                "bars_count": end_idx - start_idx + 1,
                "regime": labels[start_idx],
                "confidence_avg": segment_conf,
            }
        )
        start_idx = idx
    return segments


def _select_merge_side(
    left_segment: dict[str, Any] | None,
    right_segment: dict[str, Any] | None,
) -> str | None:
    if left_segment and right_segment:
        left_conf = float(left_segment["confidence_avg"])
        right_conf = float(right_segment["confidence_avg"])
        if left_conf > right_conf:
            return "left"
        if right_conf > left_conf:
            return "right"
        left_bars = int(left_segment["bars_count"])
        right_bars = int(right_segment["bars_count"])
        if left_bars > right_bars:
            return "left"
        if right_bars > left_bars:
            return "right"
        return "left"
    if left_segment:
        return "left"
    if right_segment:
        return "right"
    return None


def build_regime_segments(label_frame: pd.DataFrame, *, min_segment_bars: int = MIN_SEGMENT_BARS) -> pd.DataFrame:
    if label_frame.empty:
        return pd.DataFrame(
            columns=["regime", "segment_start", "segment_end", "confidence_avg", "bars_count", "meta_json"]
        )
    safe_min = max(1, int(min_segment_bars))
    ordered = label_frame.sort_values("timestamp").reset_index(drop=True)
    labels = [str(value) for value in ordered["regime"].tolist()]
    confidences = [float(value) for value in ordered["confidence"].tolist()]
    timestamps = [pd.Timestamp(value) for value in ordered["timestamp"].tolist()]
    meta_records = list(ordered["meta_json"].tolist()) if "meta_json" in ordered.columns else [{} for _ in labels]

    while True:
        segments = _segment_boundaries(labels, confidences)
        short_indices = [idx for idx, seg in enumerate(segments) if int(seg["bars_count"]) < safe_min]
        if not short_indices or len(segments) <= 1:
            break
        target_idx = short_indices[0]
        short_segment = segments[target_idx]
        left_segment = segments[target_idx - 1] if target_idx > 0 else None
        right_segment = segments[target_idx + 1] if (target_idx + 1) < len(segments) else None
        merge_side = _select_merge_side(left_segment, right_segment)
        if merge_side is None:
            break
        merge_label = str(left_segment["regime"] if merge_side == "left" else right_segment["regime"])
        for pos in range(int(short_segment["start_idx"]), int(short_segment["end_idx"]) + 1):
            labels[pos] = merge_label

    final_segments = _segment_boundaries(labels, confidences)
    rows: list[dict[str, Any]] = []
    for segment in final_segments:
        start_idx = int(segment["start_idx"])
        end_idx = int(segment["end_idx"])
        rows.append(
            {
                "regime": str(segment["regime"]),
                "segment_start": _to_utc_iso(timestamps[start_idx]),
                "segment_end": _to_utc_iso(timestamps[end_idx]),
                "confidence_avg": float(segment["confidence_avg"]),
                "bars_count": int(segment["bars_count"]),
                "meta_json": {
                    "min_segment_bars": safe_min,
                    "uncertain_share": round(
                        float(
                            sum(
                                1
                                for meta in meta_records[start_idx : end_idx + 1]
                                if bool(dict((meta or {}).get("components") or {}).get("uncertain"))
                                or str((meta or {}).get("raw_regime") or "").strip().upper() == TRANSITION_OVERLAY
                            )
                            / max(1, end_idx - start_idx + 1)
                        ),
                        6,
                    ),
                },
            }
        )
    return pd.DataFrame(rows)


def run_model_rebuild(request: ModelRebuildRequest) -> ModelRebuildResponse:
    experiment = get_lab_experiment(request.experiment_id)
    if experiment is None:
        raise ValueError(f"Unknown experiment: {request.experiment_id}")

    classifier_type = normalize_classifier_type(request.classifier_type)
    classifier_config = resolve_classifier_config(classifier_type, request.classifier_config)
    update_lab_experiment_status(experiment.id, "running_model_rebuild")
    snapshot_manifest = build_historical_snapshot(experiment.id)
    snapshot_frame = pd.read_parquet(snapshot_manifest.snapshot_path)
    feature_frame = compute_features_from_snapshot(snapshot_manifest.snapshot_path)
    classified = classify_features(
        feature_frame,
        classifier_type=classifier_type,
        classifier_config=classifier_config,
    )
    diagnostics = summarize_classification_diagnostics(classified)
    regime_timeframe = experiment_regime_timeframe(experiment)
    execution_timeframe = experiment_execution_timeframe(experiment)
    no_lookahead_validation = validate_no_lookahead_on_frame(
        snapshot_frame,
        sample_bars=int(classifier_config.get("validation_sample_bars", NO_LOOKAHEAD_VALIDATION_BARS)),
        classifier_type=classifier_type,
        classifier_config=classifier_config,
    )

    version_key = request.version_key or (
        f"rm_{experiment.id}_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    )
    model_version = create_or_update_model_version(
        version_key=version_key,
        program_id=experiment.program_id,
        experiment_id=experiment.id,
        status="active",
        notes=request.notes,
        config_json={
            "taxonomy": list(REGIME_TAXONOMY),
            "classifier": {
                "type": classifier_type,
                "config": dict(classifier_config),
            },
            "timeframes": {
                "regime_timeframe": regime_timeframe,
                "execution_timeframe": execution_timeframe,
            },
            "hysteresis": {
                "min_dwell_bars": MIN_HYSTERESIS_DWELL_BARS,
                "min_conf_delta": MIN_HYSTERESIS_CONF_DELTA,
            },
            "snapshot_hash": snapshot_manifest.snapshot_hash,
            "snapshot_path": snapshot_manifest.snapshot_path,
            "diagnostics": diagnostics,
            "validation": {
                "no_lookahead": no_lookahead_validation,
            },
        },
    )

    label_rows = [
        {
            "ts": _to_utc_iso(row["timestamp"]),
            "regime": str(row["regime"]),
            "confidence": float(row["confidence"]),
            "meta_json": dict(row["meta_json"] or {}),
        }
        for _, row in classified.iterrows()
    ]
    labels_persisted = replace_regime_labels(
        model_version_id=model_version.id,
        symbol=experiment.symbol,
        timeframe=regime_timeframe,
        labels=label_rows,
    )
    update_lab_experiment_status(experiment.id, "model_ready")

    return ModelRebuildResponse(
        status="ok",
        experiment_id=experiment.id,
        model_version_id=model_version.id,
        labels_persisted=labels_persisted,
        snapshot_path=snapshot_manifest.snapshot_path,
        snapshot_hash=snapshot_manifest.snapshot_hash,
        classifier_type=classifier_type,
        diagnostics=diagnostics,
    )


def run_segment_build(request: SegmentBuildRequest) -> SegmentBuildResponse:
    model_version = get_model_version(request.model_version_id)
    if model_version is None:
        raise ValueError(f"Unknown model version: {request.model_version_id}")

    labels = get_regime_labels(model_version_id=model_version.id)
    if not labels:
        raise ValueError(f"No regime labels found for model version: {request.model_version_id}")

    label_frame = pd.DataFrame(
        [
            {
                "timestamp": pd.to_datetime(label.ts, utc=True),
                "regime": label.regime,
                "confidence": float(label.confidence),
                "meta_json": dict(label.meta_json or {}),
            }
            for label in labels
        ]
    )
    segments = build_regime_segments(
        label_frame,
        min_segment_bars=max(MIN_SEGMENT_BARS, int(request.min_segment_bars)),
    )
    if segments.empty:
        return SegmentBuildResponse(status="ok", model_version_id=model_version.id, segments_persisted=0)

    first_label = labels[0]
    persisted = replace_regime_segments(
        model_version_id=model_version.id,
        symbol=first_label.symbol,
        timeframe=first_label.timeframe,
        segments=[
            {
                "regime": str(row["regime"]),
                "segment_start": str(row["segment_start"]),
                "segment_end": str(row["segment_end"]),
                "confidence_avg": float(row["confidence_avg"]),
                "bars_count": int(row["bars_count"]),
                "meta_json": dict(row["meta_json"] or {}),
            }
            for _, row in segments.iterrows()
        ],
    )
    return SegmentBuildResponse(
        status="ok",
        model_version_id=model_version.id,
        segments_persisted=persisted,
    )


def run_model_rebuild_job(payload: dict[str, Any]) -> dict[str, Any]:
    request = ModelRebuildRequest.model_validate(payload or {})
    response = run_model_rebuild(request)
    return response.model_dump()


def run_segment_build_job(payload: dict[str, Any]) -> dict[str, Any]:
    request = SegmentBuildRequest.model_validate(payload or {})
    response = run_segment_build(request)
    return response.model_dump()
