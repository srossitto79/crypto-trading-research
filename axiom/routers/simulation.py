"""Simulation API Router."""

import os

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from axiom.api_security import require_operator_access
from axiom.simulation import stop_simulation, kv_get, get_db

router = APIRouter(prefix="/api/simulation", tags=["simulation"], dependencies=[Depends(require_operator_access)])


def simulation_api_enabled() -> bool:
    raw = str(os.getenv("AXIOM_ENABLE_SIMULATION_API", "") or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}

class SimulationStartRequest(BaseModel):
    start_date: str
    end_date: str
    speed: float = 1.0
    interval: str = "1h"
    initial_equity: float = 10000.0
    exec_mode: str = "direct"


class StrategyValidateRequest(BaseModel):
    strategy_id: str
    strategy_type: str
    symbol: str
    params: dict = Field(default_factory=dict)

@router.post("/start")
async def api_start_simulation(req: SimulationStartRequest):
    # CLEANUP (dead-sim-endpoint): the simulation start endpoint has been retired.
    # Return 410 Gone (not 403) so callers learn the resource is permanently
    # removed rather than merely forbidden. A stale frontend wrapper
    # (frontend/src/lib/api/simulation.ts startSimulation) still references this
    # path but is itself uncalled, so the route is kept as a 410 tombstone.
    raise HTTPException(
        status_code=410,
        detail="The simulation start endpoint has been retired and is no longer available.",
    )

@router.post("/stop")
async def api_stop_simulation():
    return await stop_simulation()

@router.get("/status")
async def api_get_status():
    default_state = {
        "active": False,
        "phase": "idle",
        "current_time": "",
        "progress": 0,
        "bar": 0,
        "total_bars": 0,
        "equity": 10000.0,
        "exec_mode": "direct",
        "prices": {}
    }
    state = kv_get("simulation_state", default_state)
    # Ensure all default keys exist
    for k, v in default_state.items():
        if k not in state:
            state[k] = v
    return state

@router.get("/analytics")
async def api_get_analytics():
    return kv_get("simulation_analytics", {})

@router.get("/trades")
async def api_get_trades():
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM trades WHERE execution_type='simulation' ORDER BY opened_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]

@router.get("/equity-curve")
async def api_get_equity_curve():
    analytics = kv_get("simulation_analytics", {})
    return analytics.get("equity_curve", [])


@router.post("/validate-strategy")
async def api_validate_strategy(req: StrategyValidateRequest):
    """Run deterministic strategy validation without agent-task delegation."""
    try:
        from axiom.evolution import run_backtest_validation

        return await run_backtest_validation(
            strategy_id=req.strategy_id,
            strategy_type=req.strategy_type,
            symbol=req.symbol,
            params=req.params,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
