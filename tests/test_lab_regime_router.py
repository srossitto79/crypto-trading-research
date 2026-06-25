from __future__ import annotations

import importlib
from uuid import uuid4

import pandas as pd
from fastapi.testclient import TestClient

from axiom.api import app
from axiom.lab_db import create_lab_experiment, create_or_update_model_version, get_lab_experiment, list_lab_jobs
from axiom.lab_models import LabJobState
from axiom.lab_models import DispatchPaperIntentResponse, SelectorDecisionResponse


def test_lab_regime_experiment_enqueue_and_job_lookup():
    client = TestClient(app)

    response = client.post("/api/lab/regime/experiments", json={})
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "queued"
    assert payload["job_state"] == "queued"
    assert payload["job_id"]
    assert payload["experiment_id"]
    assert payload["regime_timeframe"] == "1h"
    assert payload["execution_timeframe"] == "15m"

    job_res = client.get(f"/api/lab/regime/jobs/{payload['job_id']}")
    assert job_res.status_code == 200
    job_payload = job_res.json()
    assert job_payload["status"] == "ok"
    assert job_payload["job"]["id"] == payload["job_id"]
    assert job_payload["job"]["state"] == "queued"
    assert isinstance(job_payload["events"], list)


def test_lab_regime_not_implemented_and_heatmap_stubs():
    client = TestClient(app)

    model_rebuild = client.post(
        "/api/lab/regime/model/rebuild",
        json={"experiment_id": "exp_missing"},
    )
    assert model_rebuild.status_code == 404
    assert "Unknown experiment" in model_rebuild.json()["detail"]

    matrix = client.post(
        "/api/lab/regime/backtests/matrix",
        json={"model_version_id": "missing"},
    )
    assert matrix.status_code == 404
    assert "Unknown model version" in matrix.json()["detail"]

    heatmap = client.get("/api/lab/regime/reports/heatmap")
    assert heatmap.status_code == 200
    payload = heatmap.json()
    assert payload["status"] == "ok"
    assert payload["regimes"] == []
    assert payload["cells"] == []
    assert payload["summary"]["total_cells"] == 0

    worker = client.get("/api/lab/regime/worker/status")
    assert worker.status_code == 200
    worker_payload = worker.json()
    assert worker_payload["status"] == "ok"
    assert isinstance(worker_payload["worker"], dict)
    assert isinstance(worker_payload["running_jobs"], list)


def test_lab_regime_enqueue_rebuild_endpoints():
    client = TestClient(app)

    experiment = create_lab_experiment(
        experiment_id=f"exp_{uuid4().hex[:8]}",
        symbol="BTC/USDT",
        timeframe="1h",
        status="ready",
    )
    model = create_or_update_model_version(
        version_key=f"mv_{uuid4().hex[:6]}",
        experiment_id=experiment.id,
        status="ready",
    )

    model_rebuild = client.post(
        "/api/lab/regime/model/rebuild/enqueue",
        json={"experiment_id": experiment.id},
    )
    assert model_rebuild.status_code == 200
    model_payload = model_rebuild.json()
    assert model_payload["status"] == "queued"
    assert model_payload["job_state"] == "queued"

    segment_build = client.post(
        "/api/lab/regime/segments/build/enqueue",
        json={"model_version_id": model.id, "min_segment_bars": 24},
    )
    assert segment_build.status_code == 200
    segment_payload = segment_build.json()
    assert segment_payload["status"] == "queued"
    assert segment_payload["job_state"] == "queued"


def test_lab_regime_worker_start_endpoint(monkeypatch):
    client = TestClient(app)

    monkeypatch.setattr(
        "axiom.routers.lab_regime.start_lab_worker_process",
        lambda: {"status": "started", "pid": 4321, "log_path": "C:/tmp/lab_worker.log"},
    )

    response = client.post("/api/lab/regime/worker/start")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["pid"] == 4321


def test_lab_regime_worker_feed_endpoint(monkeypatch):
    client = TestClient(app)
    observed: dict[str, int] = {}

    def _fake_feed(*, limit_lines: int = 200):
        observed["limit_lines"] = limit_lines
        return {
            "path": "C:/tmp/lab_worker.log",
            "exists": True,
            "lines": ["worker boot", "matrix job running"],
            "line_count": 2,
            "truncated": False,
            "updated_at": 1710000000.0,
        }

    monkeypatch.setattr("axiom.routers.lab_regime.read_lab_worker_feed", _fake_feed)

    response = client.get("/api/lab/regime/worker/feed?limit=120")
    assert response.status_code == 200
    payload = response.json()
    assert observed["limit_lines"] == 120
    assert payload["status"] == "ok"
    assert payload["exists"] is True
    assert payload["lines"][-1] == "matrix job running"


def test_lab_regime_orchestrator_routes(monkeypatch):
    client = TestClient(app)
    start_calls: list[bool] = []

    monkeypatch.setattr(
        "axiom.routers.lab_regime.start_lab_worker_process",
        lambda: start_calls.append(True) or {"status": "started", "pid": 9876},
    )

    configure = client.post(
        "/api/lab/regime/orchestrator/configure",
        json={
            "enabled": True,
            "cadence_hours": 8,
            "strategy_sources": ["graveyard", "active"],
            "run_immediately": True,
        },
    )
    assert configure.status_code == 200
    configure_payload = configure.json()
    assert configure_payload["status"] == "ok"
    assert configure_payload["config"]["enabled"] is True
    assert configure_payload["config"]["cadence_hours"] == 8
    assert configure_payload["config"]["strategy_sources"] == ["graveyard", "active"]
    assert start_calls

    status_response = client.get("/api/lab/regime/orchestrator/status")
    assert status_response.status_code == 200
    status_payload = status_response.json()
    assert status_payload["status"] == "ok"
    assert isinstance(status_payload["active_jobs"], list)


def test_lab_regime_program_routes(monkeypatch):
    client = TestClient(app)

    monkeypatch.setattr(
        "axiom.routers.lab_regime.start_lab_worker_process",
        lambda: {"status": "started", "pid": 1111, "log_path": "C:/tmp/lab_worker.log"},
    )

    create = client.post(
        "/api/lab/regime/programs",
        json={
            "symbol": "BTC/USDT",
            "regime_timeframe": "1h",
            "execution_timeframe": "15m",
            "notes": "Primary program",
        },
    )
    assert create.status_code == 200
    create_payload = create.json()
    assert create_payload["status"] == "ok"
    assert create_payload["program"]["symbol"] == "BTC/USDT"

    active = client.get("/api/lab/regime/programs/active")
    assert active.status_code == 200
    active_payload = active.json()
    assert active_payload["program"]["id"] == create_payload["program"]["id"]

    initialize = client.post(
        "/api/lab/regime/programs/initialize",
        json={
            "program_id": create_payload["program"]["id"],
            "symbol": "BTC/USDT",
            "regime_timeframe": "1h",
            "execution_timeframe": "15m",
            "notes": "Initialize baseline",
        },
    )
    assert initialize.status_code == 200
    init_payload = initialize.json()
    assert init_payload["status"] == "queued"
    assert init_payload["program_id"] == create_payload["program"]["id"]
    assert init_payload["rebuild_job_state"] == "queued"


def test_lab_regime_initialize_program_is_idempotent(monkeypatch):
    client = TestClient(app)

    monkeypatch.setattr(
        "axiom.routers.lab_regime.start_lab_worker_process",
        lambda: {"status": "started", "pid": 1111, "log_path": "C:/tmp/lab_worker.log"},
    )

    create = client.post(
        "/api/lab/regime/programs",
        json={
            "symbol": "BTC/USDT",
            "regime_timeframe": "1h",
            "execution_timeframe": "15m",
        },
    )
    assert create.status_code == 200
    program_id = create.json()["program"]["id"]

    first = client.post(
        "/api/lab/regime/programs/initialize",
        json={
            "program_id": program_id,
            "symbol": "BTC/USDT",
            "regime_timeframe": "1h",
            "execution_timeframe": "15m",
        },
    )
    assert first.status_code == 200
    first_payload = first.json()
    assert first_payload["status"] == "queued"

    second = client.post(
        "/api/lab/regime/programs/initialize",
        json={
            "program_id": program_id,
            "symbol": "BTC/USDT",
            "regime_timeframe": "1h",
            "execution_timeframe": "15m",
        },
    )
    assert second.status_code == 200
    second_payload = second.json()
    assert second_payload["status"] == "already_queued"
    assert second_payload["rebuild_job_id"] == first_payload["rebuild_job_id"]

    setup_jobs = [
        job for job in list_lab_jobs(states=[LabJobState.QUEUED, LabJobState.RUNNING], limit=100)
        if job.program_id == program_id
        and job.job_type == "model_rebuild"
        and bool((job.payload_json or {}).get("program_initialize"))
    ]
    assert len(setup_jobs) == 1

    experiment = get_lab_experiment(first_payload["experiment_id"])
    assert experiment is not None
    assert experiment.train_start is not None
    assert experiment.train_end is not None
    assert experiment.test_start is not None
    assert experiment.test_end is not None


def test_lab_regime_pool_report_route(monkeypatch):
    client = TestClient(app)

    monkeypatch.setattr(
        "axiom.routers.lab_regime.inspect_strategy_pool",
        lambda strategy_sources: {
            "requested_sources": list(strategy_sources),
            "included": [{"strategy_id": "S1", "source_pool": "graveyard"}],
            "skipped": [{"strategy_id": "S2", "reason": "broken"}],
            "counts": {"included": 1, "skipped": 1},
        },
    )

    response = client.get("/api/lab/regime/pool?source=graveyard")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["requested_sources"] == ["graveyard"]
    assert payload["included"][0]["strategy_id"] == "S1"
    assert payload["skipped"][0]["reason"] == "broken"


def test_lab_regime_timeline_normalizes_core_regimes_and_uncertainty(monkeypatch):
    client = TestClient(app)

    class _Model:
        experiment_id = "exp_test"
        config_json = {
            "snapshot_path": "C:/tmp/regime_snapshot.parquet",
            "timeframes": {"regime_timeframe": "1h", "execution_timeframe": "15m"},
            "classifier": {"type": "gmm_v1"},
            "diagnostics": {
                "bars_classified": 12,
                "uncertain_share": 0.125,
                "raw_uncertain_share": 0.25,
                "median_segment_bars": 24.0,
            },
            "validation": {"no_lookahead": {"passed": True}},
        }

    class _Segment:
        def __init__(self):
            self.regime = "TREND_UP_LOW_VOL"
            self.meta_json = {"uncertain_share": 0.05}

        def model_dump(self):
            return {
                "id": "seg_1",
                "model_version_id": "mv_test",
                "symbol": "BTC/USDT",
                "timeframe": "1h",
                "regime": self.regime,
                "segment_start": "2026-03-01T00:00:00+00:00",
                "segment_end": "2026-03-02T00:00:00+00:00",
                "confidence_avg": 0.81,
                "bars_count": 24,
                "meta_json": dict(self.meta_json),
            }

    class _Label:
        def __init__(self):
            self.regime = "TRANSITION"
            self.meta_json = {
                "raw_regime": "TRANSITION",
                "components": {
                    "mapped_regime": "RANGE",
                    "uncertain": True,
                    "overlay_regime": "TRANSITION",
                },
            }

        def model_dump(self):
            return {
                "id": "lbl_1",
                "model_version_id": "mv_test",
                "symbol": "BTC/USDT",
                "timeframe": "1h",
                "ts": "2026-03-02T00:00:00+00:00",
                "regime": self.regime,
                "confidence": 0.63,
                "meta_json": dict(self.meta_json),
            }

    monkeypatch.setattr("axiom.routers.lab_regime.get_model_version", lambda _model_version_id: _Model())
    monkeypatch.setattr("axiom.routers.lab_regime.get_regime_segments", lambda **_kwargs: [_Segment()])
    monkeypatch.setattr("axiom.routers.lab_regime.get_regime_labels", lambda **_kwargs: [_Label()])
    monkeypatch.setattr("axiom.routers.lab_regime.Path.exists", lambda _self: True)
    monkeypatch.setattr(
        "axiom.routers.lab_regime.pd.read_parquet",
        lambda *_args, **_kwargs: pd.DataFrame(
            {
                "timestamp": ["2026-03-02T00:00:00+00:00"],
                "close": [102.5],
            }
        ),
    )

    response = client.get("/api/lab/regime/reports/timeline?model_version_id=mv_test")
    assert response.status_code == 200
    payload = response.json()
    assert payload["taxonomy"] == ["TREND_UP", "TREND_DOWN", "RANGE", "HIGH_VOL"]
    assert payload["summary"]["current_regime"] == "RANGE"
    assert payload["summary"]["current_uncertain"] is True
    assert payload["segments"][0]["core_regime"] == "TREND_UP"
    assert payload["segments"][0]["uncertain_share"] == 0.05
    assert payload["labels"][0]["core_regime"] == "RANGE"
    assert payload["labels"][0]["uncertain"] is True
    assert payload["price_points"][0]["close"] == 102.5
    assert payload["price_points"][0]["normalized_close"] == 1.0


def test_lab_regime_heatmap_marks_error_and_summary(monkeypatch):
    client = TestClient(app)

    class _Model:
        config_json = {"timeframes": {"regime_timeframe": "1h", "execution_timeframe": "15m"}}

    monkeypatch.setattr("axiom.routers.lab_regime.get_model_version", lambda _model_version_id: _Model())
    monkeypatch.setattr(
        "axiom.routers.lab_regime.list_strategy_regime_scores",
        lambda _model_version_id: [
            {
                "regime": "TRANSITION",
                "strategy_id": "S1",
                "score": 0.0,
                "updated_at": "2026-03-19T00:00:00+00:00",
                "metrics_json": {
                    "raw": {"total_return_pct": 0.0},
                    "adjusted": {"total_return_pct": 0.0, "profit_factor": 0.0, "sharpe": 0.0},
                    "oos_adjusted": {"total_return_pct": 0.0, "profit_factor": 0.0},
                    "strategy_meta": {"source_pool": "graveyard"},
                    "diagnostics": {
                        "train": {
                            "status": "error",
                            "error": "Local backtesting does not yet enforce these risk controls: risk_pct.",
                        },
                        "oos": {"status": "error", "error": "Local backtesting does not yet enforce these risk controls: risk_pct."},
                    },
                },
                "admission_json": {"admitted": False, "reasons": ["train_backtest_error", "oos_backtest_error"]},
            }
        ],
    )

    response = client.get("/api/lab/regime/reports/heatmap?model_version_id=mv_test")
    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["error_cells"] == 1
    assert payload["cells"][0]["state"] == "error"
    assert "risk_pct" in payload["cells"][0]["primary_reason"]


def test_lab_regime_heatmap_normalizes_legacy_regimes(monkeypatch):
    client = TestClient(app)

    class _Model:
        config_json = {
            "timeframes": {"regime_timeframe": "1h", "execution_timeframe": "15m"},
            "classifier": {"type": "gmm_v1"},
            "diagnostics": {"uncertain_share": 0.1, "bars_classified": 48},
        }

    monkeypatch.setattr("axiom.routers.lab_regime.get_model_version", lambda _model_version_id: _Model())
    monkeypatch.setattr(
        "axiom.routers.lab_regime.list_strategy_regime_scores",
        lambda _model_version_id: [
            {
                "regime": "TREND_UP_LOW_VOL",
                "strategy_id": "S1",
                "score": 0.82,
                "updated_at": "2026-03-19T00:00:00+00:00",
                "metrics_json": {
                    "raw": {"total_return_pct": 0.12},
                    "adjusted": {"total_return_pct": 0.09, "profit_factor": 1.4, "sharpe": 1.1},
                    "oos_adjusted": {"total_return_pct": 0.04, "profit_factor": 1.2},
                    "strategy_meta": {"source_pool": "active", "candidate_key": "S1"},
                    "diagnostics": {"train": {"status": "ok"}, "oos": {"status": "ok"}},
                },
                "admission_json": {"admitted": True, "reasons": []},
            }
        ],
    )

    response = client.get("/api/lab/regime/reports/heatmap?model_version_id=mv_test")
    assert response.status_code == 200
    payload = response.json()
    assert payload["taxonomy"] == ["TREND_UP", "TREND_DOWN", "RANGE", "HIGH_VOL"]
    assert payload["regimes"] == ["TREND_UP"]
    assert payload["cells"][0]["regime"] == "TREND_UP"
    assert payload["cells"][0]["legacy_regime"] == "TREND_UP_LOW_VOL"
    assert payload["summary"]["legacy_cells"] == 1
    assert payload["summary"]["classifier_type"] == "gmm_v1"


def test_lab_regime_selector_and_dispatch_endpoints(monkeypatch):
    client = TestClient(app)

    monkeypatch.setattr(
        "axiom.routers.lab_regime.decide_current_regime",
        lambda *_args, **_kwargs: SelectorDecisionResponse(
            status="ok",
            model_version_id="mv_1",
            symbol="BTC/USDT",
            timeframe="1h",
            regime_timeframe="1h",
            execution_timeframe="15m",
            decision="no_trade",
            regime="TRANSITION",
            confidence=0.45,
            champion_strategy_id=None,
            blocked_reason="no_trade:cold_start",
            selection_event_id="lse_1",
            meta_json={},
        ),
    )
    selector = client.post("/api/lab/regime/selector/decide", json={"model_version_id": "mv_1"})
    assert selector.status_code == 200
    selector_payload = selector.json()
    assert selector_payload["blocked_reason"] == "no_trade:cold_start"
    assert selector_payload["execution_timeframe"] == "15m"

    monkeypatch.setattr(
        "axiom.routers.lab_regime.dispatch_paper_intent",
        lambda *_args, **_kwargs: DispatchPaperIntentResponse(
            status="ok",
            action="long_entry",
            intent_id="lsi_1",
            selection_event_id="lse_1",
            trade_id=None,
            execution_status="blocked",
            reason="no_trade:cold_start",
            fill_price=None,
            slippage_bps=None,
            feedback_id="lef_1",
            payload={},
        ),
    )
    dispatch = client.post(
        "/api/lab/regime/intents/dispatch-paper",
        json={"action": "long_entry", "selection_event_id": "lse_1"},
    )
    assert dispatch.status_code == 200
    dispatch_payload = dispatch.json()
    assert dispatch_payload["execution_status"] == "blocked"


def test_backtests_matrix_enqueue_is_queue_only():
    client = TestClient(app)

    experiment = create_lab_experiment(
        experiment_id=f"exp_{uuid4().hex[:8]}",
        symbol="BTC/USDT",
        timeframe="1h",
        status="ready",
    )
    model = create_or_update_model_version(
        version_key=f"test-matrix-{uuid4().hex[:6]}",
        experiment_id=experiment.id,
        status="ready",
    )

    response = client.post(
        "/api/lab/regime/backtests/matrix",
        json={"model_version_id": model.id, "max_strategies": 2, "strategy_sources": ["active", "graveyard"]},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "queued"
    assert payload["job_id"]

    job_payload = client.get(f"/api/lab/regime/jobs/{payload['job_id']}").json()
    assert job_payload["job"]["state"] == "queued"
    assert job_payload["job"]["payload_json"]["strategy_sources"] == ["active", "graveyard"]


def test_lab_regime_router_not_mounted_when_feature_is_dormant(monkeypatch):
    import axiom.api as api_mod

    monkeypatch.setenv("AXIOM_ENABLE_REGIME_LAB", "0")
    disabled_api = importlib.reload(api_mod)

    try:
        client = TestClient(disabled_api.app)
        response = client.get("/api/lab/regime/worker/health")
        assert response.status_code == 404
    finally:
        monkeypatch.setenv("AXIOM_ENABLE_REGIME_LAB", "1")
        restored_api = importlib.reload(api_mod)
        globals()["app"] = restored_api.app
