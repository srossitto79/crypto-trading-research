"""API domain layer for Bot Factory — delegates to db and manager."""

from __future__ import annotations

import json

from axiom.db import (
    clone_bot,
    create_bot,
    delete_bot,
    get_bot,
    get_bot_config_versions,
    get_bot_decisions,
    get_bot_equity_state,
    get_bot_trades,
    get_open_bot_positions,
    list_bots,
    update_bot,
    create_bot_template,
    delete_bot_template,
    get_bot_template,
    list_bot_templates,
)


def api_list_bots() -> list[dict]:
    return list_bots()


def api_get_bot(bot_id: str) -> dict | None:
    return get_bot(bot_id)


def api_create_bot(config: dict) -> dict:
    bot_id = create_bot(config)
    return get_bot(bot_id)


def api_update_bot(bot_id: str, updates: dict) -> dict | None:
    update_bot(bot_id, updates)
    return get_bot(bot_id)


def api_delete_bot(bot_id: str) -> dict:
    # Stop the bot first so we don't delete config out from under a live
    # subprocess that's still writing trades.
    try:
        from axiom.bot_factory.manager import BotManager

        BotManager.get_instance().stop_bot(bot_id)
    except Exception:
        pass
    # Close any OPEN paper trades so they don't linger as phantom exposure once
    # the config (and thus attribution) is gone.
    try:
        from axiom.db import close_open_bot_trades

        close_open_bot_trades(bot_id, reason="bot_deleted")
    except Exception:
        pass
    # Drop the bot's isolated ChromaDB memory collection (unbounded leak otherwise).
    try:
        from axiom.bot_factory.memory import BotMemory

        BotMemory(bot_id).delete_collection()
    except Exception:
        pass
    delete_bot(bot_id)
    return {"status": "deleted", "bot_id": bot_id}


def api_clone_bot(bot_id: str, new_name: str) -> dict:
    new_id = clone_bot(bot_id, new_name)
    return get_bot(new_id)


def api_start_bot(bot_id: str) -> dict:
    from axiom.bot_factory.manager import BotManager
    manager = BotManager.get_instance()
    return manager.start_bot(bot_id)


def api_stop_bot(bot_id: str) -> dict:
    from axiom.bot_factory.manager import BotManager
    manager = BotManager.get_instance()
    return manager.stop_bot(bot_id)


def api_kill_all() -> dict:
    from axiom.bot_factory.manager import BotManager
    manager = BotManager.get_instance()
    return manager.kill_all()


def api_get_decisions(bot_id: str, limit: int = 50) -> list[dict]:
    return get_bot_decisions(bot_id, limit=limit)


def api_get_trades(bot_id: str, limit: int = 50) -> list[dict]:
    return get_bot_trades(bot_id, limit=limit)


def api_get_stats(bot_id: str) -> dict:
    """Aggregate trade stats over ALL of a bot's trades (server-side, uncapped)."""
    from axiom.db import get_bot_trade_stats

    return get_bot_trade_stats(bot_id)


def api_get_positions(bot_id: str) -> dict:
    """Return open positions (with SL/TP levels) plus equity state so the UI
    can render a live snapshot matching what the runner sees in memory."""
    positions = get_open_bot_positions(bot_id)
    equity = get_bot_equity_state(bot_id) or {}
    bot = get_bot(bot_id) or {}
    starting_capital = float(bot.get("capital_allocation") or 0)
    realized_pnl = float(equity.get("realized_pnl") or 0)
    peak_equity = equity.get("peak_equity")
    return {
        "bot_id": bot_id,
        "starting_capital": starting_capital,
        "realized_pnl": realized_pnl,
        "peak_equity": float(peak_equity) if peak_equity is not None else None,
        "equity_state_started_at": equity.get("equity_state_started_at"),
        "open_positions": positions,
    }


def api_get_versions(bot_id: str) -> list[dict]:
    return get_bot_config_versions(bot_id)


def api_get_memory(bot_id: str, limit: int = 50) -> list[dict]:
    """Return recent entries from this bot's isolated ChromaDB collection."""
    from axiom.bot_factory.memory import BotMemory
    return BotMemory(bot_id).list_recent(limit=limit)


def api_diff_versions(bot_id: str, v1: int, v2: int) -> dict:
    """Diff two config versions field-by-field."""
    versions = get_bot_config_versions(bot_id)
    version_map = {v["version"]: v.get("config_snapshot", {}) for v in versions}

    snap1 = version_map.get(v1)
    snap2 = version_map.get(v2)

    if snap1 is None or snap2 is None:
        return {"error": "Version not found", "available": list(version_map.keys())}

    if isinstance(snap1, str):
        snap1 = json.loads(snap1)
    if isinstance(snap2, str):
        snap2 = json.loads(snap2)

    diff = {}
    all_keys = set(list(snap1.keys()) + list(snap2.keys()))
    for key in sorted(all_keys):
        val1 = snap1.get(key)
        val2 = snap2.get(key)
        if val1 != val2:
            diff[key] = {"v1": val1, "v2": val2}

    return {"v1": v1, "v2": v2, "changes": diff}


# ── Templates ────────────────────────────────────────────────────────


def api_list_templates() -> list[dict]:
    return list_bot_templates()


def api_get_template(template_id: str) -> dict | None:
    return get_bot_template(template_id)


def api_create_template(name: str, description: str | None, config: dict) -> dict:
    template_id = create_bot_template(name, description, config)
    return get_bot_template(template_id)


def api_delete_template(template_id: str) -> dict:
    delete_bot_template(template_id)
    return {"status": "deleted", "template_id": template_id}


# ── Strategy-to-Bot Bridge ──────────────────────────────────────────


def api_create_bot_from_strategy(strategy_id: str) -> dict:
    """Create a bot config pre-filled from a strategy container."""
    from axiom.db import get_db

    with get_db() as conn:
        row = conn.execute(
            "SELECT id, name, type, symbol, timeframe, params, metrics FROM strategies WHERE id = ?",
            (strategy_id,),
        ).fetchone()
        if not row:
            raise ValueError(f"Strategy {strategy_id} not found")

        strategy = dict(row)

    params = strategy.get("params")
    if isinstance(params, str):
        import json as _json
        try:
            params = _json.loads(params)
        except Exception:
            params = {}

    # strategies.symbol is a bare base ("BTC", "SOL"); the runner's market fetch
    # needs a full pair ("BTC/USDT"), so normalize before locking (BFAPI-8).
    raw_symbol = strategy.get("symbol")
    pair = None
    if raw_symbol:
        pair = raw_symbol if "/" in raw_symbol else f"{raw_symbol}/USDT"

    orig_tf = strategy.get("timeframe", "1h")
    config = {
        "name": f"Bot from {strategy.get('name', strategy_id)}",
        # No hardcoded model — inherits the operator's configured default at create.
        "context": (
            f"This bot is seeded from strategy {strategy_id} ({strategy.get('name', '')}).\n"
            f"Type: {strategy.get('type', 'unknown')}\n"
            f"Symbol: {pair or 'unknown'}\n"
            f"Original strategy timeframe: {orig_tf} — NOTE: this bot observes 1-hour candles.\n"
            f"Parameters: {params}"
        ),
        "strategy": (
            f"Trade a {strategy.get('type', 'unknown')} strategy on {pair or 'the locked pair'} "
            f"using the 1-hour candles you are given. Use the parameters and rules above as guidance, "
            f"adapting them to the 1-hour timeframe."
        ),
        "asset_mode": "locked",
        "locked_pairs": [pair] if pair else None,
    }

    return {"config": config, "strategy_id": strategy_id}
