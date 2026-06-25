from fastapi import APIRouter, Depends, HTTPException

from axiom import api_core as core
from axiom.api_domains import trading as trading_domain
from axiom.api_security import require_operator_access

router = APIRouter(tags=["trading"])


@router.get("/api/trades/open")
def read_open_trades(verify_exchange: bool | None = None, stale_grace_seconds: int = 180):
    return trading_domain.read_open_trades(verify_exchange=verify_exchange, stale_grace_seconds=stale_grace_seconds)


@router.get("/api/trades/recent")
def read_recent_trades(limit: int = 20):
    return trading_domain.read_recent_trades(limit=limit)


@router.get("/api/trades")
def read_all_trades(status: str | None = None, limit: int = 200, offset: int = 0):
    """Full trade ledger across all statuses (OPEN/CLOSED/FAILED), filterable + paged."""
    return trading_domain.read_all_trades(status=status, limit=limit, offset=offset)


# NOTE: registered under the canonical /api/strategies namespace, NOT /api/Axiom.
# The AxiomV1CompatMiddleware rewrites every /api/Axiom/* path to /api/* before
# routing, so a route registered under /api/Axiom/* is unreachable (the rewritten
# request never matches it). Frontend calls /strategies/{id}/live-* (-> /api/...).
@router.get("/api/strategies/{strategy_id}/live-indicators")
def read_live_indicators(strategy_id: str, timeframe: str | None = None, limit: int = 500):
    """Indicator series + config for a live/deployed strategy's chart (mirrors paper)."""
    return trading_domain.read_live_indicators(strategy_id, timeframe=timeframe, limit=limit)


@router.get("/api/strategies/{strategy_id}/live-markers")
def read_live_markers(strategy_id: str, limit: int = 500, include_generated: bool = False):
    """Entry/exit/blocked chart markers for a live/deployed strategy (mirrors paper)."""
    return trading_domain.read_live_markers(strategy_id, limit=limit, include_generated=include_generated)


@router.get("/api/strategies/{strategy_id}/live-signals")
def read_live_signals(strategy_id: str):
    """Runtime indicators + pending ('approaching') signals for a live strategy (mirrors paper)."""
    return trading_domain.read_live_signals(strategy_id)


@router.post("/api/trades/{trade_id}/mark-failed", dependencies=[Depends(require_operator_access)])
def mark_trade_failed(trade_id: str, body: core.MarkTradeFailedBody):
    result = trading_domain.mark_trade_failed(trade_id, body)
    if isinstance(result, dict) and not result.get("ok", False):
        raise HTTPException(status_code=400, detail=str(result.get("error") or "mark-failed failed"))
    return result


@router.post("/api/trades/{trade_id}/force-close", dependencies=[Depends(require_operator_access)])
def force_close_trade(trade_id: str, body: core.ForceCloseTradeBody):
    result = trading_domain.force_close_trade(trade_id, body)
    # A failed force-close must NOT look like success — the position may still
    # be open on the exchange. Surface it as a non-2xx so the client treats it
    # as an error instead of silently refreshing as if the close landed.
    if isinstance(result, dict) and not result.get("ok", False):
        raise HTTPException(status_code=502, detail=str(result.get("error") or "force-close failed"))
    return result
