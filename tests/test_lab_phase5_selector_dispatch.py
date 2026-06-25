from __future__ import annotations

import pandas as pd

from axiom.db import get_db
from axiom.lab_db import (
    create_lab_experiment,
    create_or_update_model_version,
    create_selection_event,
    get_lab_db,
    get_selection_event,
    replace_regime_containers,
    replace_regime_segments,
)
from axiom.lab_intent_dispatch import dispatch_paper_intent
from axiom.lab_models import DispatchPaperIntentRequest, SelectorDecideRequest
from axiom.lab_selector import decide_current_regime


def _bootstrap_model(symbol: str = "BTC/USDT", timeframe: str = "1h") -> str:
    experiment = create_lab_experiment(
        experiment_id="exp_phase5",
        symbol=symbol,
        timeframe=timeframe,
        status="ready",
    )
    model = create_or_update_model_version(
        version_key="mv_phase5",
        experiment_id=experiment.id,
        status="active",
    )
    replace_regime_segments(
        model_version_id=model.id,
        symbol=symbol,
        timeframe=timeframe,
        segments=[
            {
                "regime": "TREND_UP_LOW_VOL",
                "segment_start": "2026-01-01T00:00:00Z",
                "segment_end": "2026-01-03T00:00:00Z",
                "confidence_avg": 0.82,
                "bars_count": 50,
                "meta_json": {},
            }
        ],
    )
    return model.id


def _classified_frame(regime: str, confidence: float, bars: int = 12) -> pd.DataFrame:
    timestamps = pd.date_range("2026-03-01", periods=bars, freq="h", tz="UTC")
    return pd.DataFrame(
        {
            "timestamp": timestamps,
            "raw_regime": [regime] * bars,
            "raw_confidence": [confidence] * bars,
            "raw_meta": [{} for _ in range(bars)],
            "regime": [regime] * bars,
            "confidence": [confidence] * bars,
            "meta_json": [{} for _ in range(bars)],
        }
    )


def _classified_frame_custom(
    *,
    raw_regimes: list[str],
    regimes: list[str],
    confidence: float,
    raw_confidence: float | None = None,
    raw_meta: list[dict] | None = None,
) -> pd.DataFrame:
    bars = len(regimes)
    timestamps = pd.date_range("2026-03-01", periods=bars, freq="h", tz="UTC")
    return pd.DataFrame(
        {
            "timestamp": timestamps,
            "raw_regime": list(raw_regimes),
            "raw_confidence": [raw_confidence if raw_confidence is not None else confidence] * bars,
            "raw_meta": list(raw_meta or [{} for _ in range(bars)]),
            "regime": list(regimes),
            "confidence": [confidence] * bars,
            "meta_json": [{} for _ in range(bars)],
        }
    )


def _create_trade_selection_event(model_id: str, *, strategy_id: str = "S100") -> str:
    event = create_selection_event(
        symbol="BTC/USDT",
        timeframe="1h",
        regime="TREND_UP_LOW_VOL",
        confidence=0.84,
        champion_strategy_id=strategy_id,
        blocked_reason=None,
        decision_json={
            "decision": "trade",
            "model_version_id": model_id,
            "meta_json": {},
        },
    )
    return event.id


def test_selector_blocks_uncertain_regime_on_low_confidence(monkeypatch):
    model_id = _bootstrap_model()
    replace_regime_containers(
        model_version_id=model_id,
        score_version="v1",
        regimes=[
            {
                "regime": "TREND_UP_LOW_VOL",
                "members": [{"strategy_id": "S100", "rank": 1, "score": 0.9, "admitted": True}],
                "champion": {"strategy_id": "S100", "score": 0.9, "rationale_json": {}},
                "meta_json": {},
            }
        ],
    )

    monkeypatch.setattr("axiom.lab_selector._resolve_market_frame", lambda *_args, **_kwargs: pd.DataFrame())
    monkeypatch.setattr("axiom.lab_selector.compute_features_from_frame", lambda *_args, **_kwargs: pd.DataFrame({"x": [1]}))
    monkeypatch.setattr(
        "axiom.lab_selector.classify_features",
        lambda *_args, **_kwargs: _classified_frame("TREND_UP_LOW_VOL", 0.40),
    )

    decision = decide_current_regime(SelectorDecideRequest(model_version_id=model_id))
    assert decision.decision == "no_trade"
    assert decision.blocked_reason == "no_trade:uncertain_regime"
    assert decision.selection_event_id
    assert decision.meta_json["uncertain_regime"] is True
    event = get_selection_event(decision.selection_event_id or "")
    assert event is not None
    assert event.blocked_reason == "no_trade:uncertain_regime"


def test_selector_blocks_when_no_champion(monkeypatch):
    model_id = _bootstrap_model()

    monkeypatch.setattr("axiom.lab_selector._resolve_market_frame", lambda *_args, **_kwargs: pd.DataFrame())
    monkeypatch.setattr("axiom.lab_selector.compute_features_from_frame", lambda *_args, **_kwargs: pd.DataFrame({"x": [1]}))
    monkeypatch.setattr(
        "axiom.lab_selector.classify_features",
        lambda *_args, **_kwargs: _classified_frame("TREND_UP_LOW_VOL", 0.81),
    )

    decision = decide_current_regime(SelectorDecideRequest(model_version_id=model_id))
    assert decision.decision == "no_trade"
    assert decision.blocked_reason == "no_trade:no_champion"


def test_selector_allows_stable_regime_with_small_transition_noise(monkeypatch):
    model_id = _bootstrap_model()
    replace_regime_containers(
        model_version_id=model_id,
        score_version="v1",
        regimes=[
            {
                "regime": "TREND_UP_LOW_VOL",
                "members": [{"strategy_id": "S100", "rank": 1, "score": 0.9, "admitted": True}],
                "champion": {"strategy_id": "S100", "score": 0.9, "rationale_json": {}},
                "meta_json": {},
            }
        ],
    )

    raw_regimes = ["TREND_UP_LOW_VOL"] * 11 + ["TRANSITION"]
    regimes = ["TREND_UP_LOW_VOL"] * 12
    raw_meta = [{} for _ in range(11)] + [{"uncertain": True}]

    monkeypatch.setattr("axiom.lab_selector._resolve_market_frame", lambda *_args, **_kwargs: pd.DataFrame())
    monkeypatch.setattr("axiom.lab_selector.compute_features_from_frame", lambda *_args, **_kwargs: pd.DataFrame({"x": [1]}))
    monkeypatch.setattr(
        "axiom.lab_selector.classify_features",
        lambda *_args, **_kwargs: _classified_frame_custom(
            raw_regimes=raw_regimes,
            regimes=regimes,
            confidence=0.81,
            raw_meta=raw_meta,
        ),
    )

    decision = decide_current_regime(SelectorDecideRequest(model_version_id=model_id))

    assert decision.decision == "trade"
    assert decision.blocked_reason is None
    assert decision.champion_strategy_id == "S100"
    assert decision.meta_json["uncertain_regime"] is False
    assert decision.meta_json["raw_transition_share"] < 0.5


def test_selector_blocks_transition_state_as_uncertain_not_cold_start(monkeypatch):
    model_id = _bootstrap_model()

    monkeypatch.setattr("axiom.lab_selector._resolve_market_frame", lambda *_args, **_kwargs: pd.DataFrame())
    monkeypatch.setattr("axiom.lab_selector.compute_features_from_frame", lambda *_args, **_kwargs: pd.DataFrame({"x": [1]}))
    monkeypatch.setattr(
        "axiom.lab_selector.classify_features",
        lambda *_args, **_kwargs: _classified_frame("TRANSITION", 0.82),
    )

    decision = decide_current_regime(SelectorDecideRequest(model_version_id=model_id))

    assert decision.decision == "no_trade"
    assert decision.blocked_reason == "no_trade:uncertain_regime"
    assert decision.meta_json["transition_state"] is True
    assert decision.meta_json["unseen_regime"] is False


def test_dispatch_routes_to_paper_and_persists_feedback(AXIOM_db):
    model_id = _bootstrap_model()
    selection_event_id = _create_trade_selection_event(model_id)

    open_result = dispatch_paper_intent(
        DispatchPaperIntentRequest(
            model_version_id=model_id,
            selection_event_id=selection_event_id,
            action="long_entry",
            signal_price=100.0,
            size=1.5,
            leverage=1.0,
        )
    )
    assert open_result.execution_status == "filled"
    assert open_result.trade_id
    assert open_result.feedback_id

    close_result = dispatch_paper_intent(
        DispatchPaperIntentRequest(
            model_version_id=model_id,
            selection_event_id=selection_event_id,
            action="long_exit",
            signal_price=101.0,
        )
    )
    assert close_result.execution_status == "filled"
    assert close_result.trade_id == open_result.trade_id
    assert close_result.feedback_id

    with get_db() as conn:
        trade = conn.execute(
            "SELECT status, entry_price, exit_price FROM trades WHERE id = ?",
            (open_result.trade_id,),
        ).fetchone()
    assert trade is not None
    assert str(trade["status"]) == "CLOSED"

    with get_lab_db() as conn:
        feedback_rows = conn.execute(
            "SELECT execution_status FROM lab_execution_feedback ORDER BY created_at DESC LIMIT 2"
        ).fetchall()
    assert len(feedback_rows) == 2
    assert all(str(row["execution_status"]) == "filled" for row in feedback_rows)


def test_dispatch_uses_base_strategy_id_but_preserves_candidate_key_metadata(AXIOM_db):
    model_id = _bootstrap_model()
    event = create_selection_event(
        symbol="BTC/USDT",
        timeframe="1h",
        regime="TREND_UP_LOW_VOL",
        confidence=0.84,
        champion_strategy_id="S100:short_only",
        blocked_reason=None,
        decision_json={
            "decision": "trade",
            "model_version_id": model_id,
            "champion_meta": {
                "strategy_id": "S100",
                "candidate_key": "S100:short_only",
                "trade_mode": "short_only",
                "position_model": "single_side",
            },
            "meta_json": {},
        },
    )

    open_result = dispatch_paper_intent(
        DispatchPaperIntentRequest(
            model_version_id=model_id,
            selection_event_id=event.id,
            action="short_entry",
            signal_price=100.0,
            size=1.0,
            leverage=1.0,
        )
    )
    duplicate_result = dispatch_paper_intent(
        DispatchPaperIntentRequest(
            model_version_id=model_id,
            selection_event_id=event.id,
            action="short_entry",
            signal_price=100.0,
            size=1.0,
            leverage=1.0,
        )
    )

    assert open_result.execution_status == "filled"
    assert duplicate_result.execution_status == "rejected"
    assert duplicate_result.reason == "position_already_open"

    with get_db() as conn:
        trade = conn.execute(
            "SELECT strategy_id, direction, signal_data FROM trades WHERE id = ?",
            (open_result.trade_id,),
        ).fetchone()
    assert trade is not None
    assert str(trade["strategy_id"]) == "S100"
    assert str(trade["direction"]) == "short"
    assert "S100:short_only" in str(trade["signal_data"] or "")


def test_dispatch_requires_selection_event_id(AXIOM_db):
    model_id = _bootstrap_model()

    try:
        dispatch_paper_intent(
            DispatchPaperIntentRequest(
                model_version_id=model_id,
                action="long_entry",
                signal_price=100.0,
            )
        )
    except ValueError as exc:
        assert "selection_event_id" in str(exc)
    else:
        raise AssertionError("dispatch_paper_intent should require selection_event_id")
