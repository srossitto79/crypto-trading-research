import logging

from fastapi import HTTPException, Request, Response, WebSocket

from axiom.api_domains import analytics as analytics_domain
from axiom.api_domains import paper as paper_domain
from axiom.api_domains import tasks as tasks_domain
from axiom.api_domains import trading as trading_domain
from axiom.control_plane import ops as control_plane_ops
from axiom.control_plane import status as control_plane_status
from axiom.routers import agents as agents_routes
from axiom.routers import auth as auth_routes
from axiom.routers import strategies as strategies_routes
from axiom.routers import websockets as websockets_routes

log = logging.getLogger("axiom.legacy_api")
LEGACY_API_SUNSET_DATE = "2026-06-30"
LEGACY_API_SUNSET_HTTP = "Tue, 30 Jun 2026 00:00:00 GMT"


def apply_legacy_response_headers(response: Response, route_path: str) -> None:
    response.headers["Deprecation"] = "true"
    response.headers["Sunset"] = LEGACY_API_SUNSET_HTTP
    response.headers["X-Axiom-Legacy-Route"] = route_path
    log.warning(
        "Legacy API route used: %s (scheduled sunset %s)",
        route_path,
        LEGACY_API_SUNSET_DATE,
    )


def _parse_bool_query(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    lowered = str(value).strip().lower()
    if lowered in {"1", "true", "yes", "on", "y"}:
        return True
    if lowered in {"0", "false", "no", "off", "n"}:
        return False
    return default


def _parse_int_query(value: str | None, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(str(value).strip())
    except Exception:
        return default


def legacy_AXIOM_get(legacy_path: str, request: Request, limit: int = 50):
    """Legacy compatibility layer for older callers using `/api/Axiom/*`."""
    normalized_path = legacy_path.strip("/")

    if normalized_path == "health":
        return control_plane_status.health_check()
    if normalized_path == "stats":
        return analytics_domain.get_stats()
    if normalized_path == "dashboard":
        return control_plane_status.get_dashboard(require_account_connection=True)
    if normalized_path == "regime":
        return control_plane_status.get_regime()
    if normalized_path == "risk":
        return control_plane_status.get_risk()
    if normalized_path == "sentiment":
        return control_plane_status.get_sentiment()
    if normalized_path == "equity-history":
        return control_plane_status.get_equity_history()
    if normalized_path == "scanner/state":
        return control_plane_status.get_scanner_state()
    if normalized_path == "strategies/performance":
        return analytics_domain.get_strategy_performance()
    if normalized_path == "trades/open":
        return trading_domain.read_open_trades()
    if normalized_path == "trades/recent":
        return trading_domain.read_recent_trades(limit=_parse_int_query(request.query_params.get("limit"), 20))
    if normalized_path == "agents":
        enabled_only = _parse_bool_query(request.query_params.get("enabled_only"), False)
        return agents_routes.read_agents(enabled_only=enabled_only)
    if normalized_path == "agent-tasks":
        return tasks_domain.get_agent_tasks()
    if normalized_path == "scheduler":
        return control_plane_ops.get_scheduler()
    if normalized_path == "logs":
        logs_limit = _parse_int_query(request.query_params.get("limit"), limit)
        return control_plane_ops.get_logs(limit=logs_limit)
    if normalized_path == "results":
        results_limit = _parse_int_query(request.query_params.get("limit"), 200)
        return strategies_routes.get_backtest_results(
            strategy=request.query_params.get("strategy"),
            symbol=request.query_params.get("symbol"),
            limit=max(1, results_limit),
            remote_skip=_parse_bool_query(request.query_params.get("remote_skip"), False),
        )
    if normalized_path == "results/count":
        return strategies_routes.get_backtest_results_count()
    if normalized_path == "strategies":
        raw_strategies_limit = request.query_params.get("limit")
        strategies_limit = _parse_int_query(raw_strategies_limit, 500) if raw_strategies_limit is not None else None
        strategies_offset = _parse_int_query(request.query_params.get("offset"), 0)
        return strategies_routes.read_strategies(
            status=request.query_params.get("status"),
            limit=strategies_limit,
            offset=strategies_offset,
        )
    if normalized_path == "backtesting/status":
        return strategies_routes.get_backtesting_status(
            remote_skip=_parse_bool_query(request.query_params.get("remote_skip"), False)
        )
    if normalized_path == "evolution":
        return strategies_routes.get_evolution()
    if normalized_path == "paper/sessions":
        return paper_domain.get_paper_sessions()
    if normalized_path == "auth/providers":
        return auth_routes.get_auth_providers()
    if normalized_path == "model-policy":
        return agents_routes.get_model_policy()
    if normalized_path.startswith("agents/"):
        parts = normalized_path.split("/")
        if len(parts) == 2:
            if parts[1].lower() == "model-options":
                refresh_models = _parse_bool_query(request.query_params.get("refresh"), False)
                return agents_routes.get_agent_model_options(refresh=refresh_models)
            return agents_routes.get_agent(parts[1])
        if len(parts) == 3 and parts[2] == "documents":
            return agents_routes.get_agent_documents(parts[1])
        if len(parts) == 4 and parts[2] == "documents":
            return agents_routes.get_agent_document(parts[1], parts[3])

    raise HTTPException(status_code=404, detail=f"Legacy endpoint not supported: /api/Axiom/{normalized_path}")


def post_brain_chat_legacy(body):
    from axiom.routers import system as system_routes

    return system_routes.post_brain_chat(body)


def get_brain_chat_result_legacy(task_id: int, response: Response):
    from axiom.routers import system as system_routes

    return system_routes.get_brain_chat_result(task_id, response)


def put_legacy_model_policy(body):
    return agents_routes.put_model_policy(body)


def legacy_patch_agent(agent_id: str, body):
    return agents_routes.patch_agent(agent_id, body)


def legacy_put_agent_document(agent_id: str, document: str, body):
    return agents_routes.put_agent_document(agent_id, document, body)


def legacy_patch_agent_model(agent_id: str, body):
    return agents_routes.patch_agent_model(agent_id, body)


def legacy_post_agent_test_discord(agent_id: str, body=None):
    return agents_routes.post_agent_test_discord(agent_id, body)


async def legacy_post_agent_task_queues(body):
    return await control_plane_ops.legacy_post_agent_task_queues(body)


async def legacy_websocket_endpoint(ws: WebSocket):
    await websockets_routes.websocket_endpoint(ws)


__all__ = [
    "apply_legacy_response_headers",
    "get_brain_chat_result_legacy",
    "legacy_AXIOM_get",
    "legacy_patch_agent",
    "legacy_patch_agent_model",
    "legacy_post_agent_task_queues",
    "legacy_post_agent_test_discord",
    "legacy_put_agent_document",
    "legacy_websocket_endpoint",
    "post_brain_chat_legacy",
    "put_legacy_model_policy",
]
