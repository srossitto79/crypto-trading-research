"""Auto-parameter recalibration — adjusts strategy params on regime shifts."""

import json
import logging
from datetime import datetime, timezone

from axiom.db import get_db, kv_get, kv_set, log_activity
from axiom.regime import TRACKED_ASSETS, detect_regime, get_adjusted_params, is_strategy_allowed

log = logging.getLogger("axiom.recalibrator")


def _normalize_result(message: str, status: str, details: dict | None = None) -> dict:
    payload: dict = {"status": status, "message": message}
    if details:
        payload.update(details)
    return payload


def _persist_strategy_params(strategy_id: str, strategy_type: str, params: dict):
    """Persist updated params for deployed DB-backed strategies."""
    from axiom.brain import params_write_blocked

    with get_db() as conn:
        existing = conn.execute("SELECT id, stage FROM strategies WHERE id = ?", (strategy_id,)).fetchone()
        if not existing:
            return
        # Paper/live strategies are operator-owned: the recalibrator is a
        # background writer (not a user actor), so its param overlay is refused.
        if params_write_blocked(existing["stage"], "recalibrator"):
            log.info(
                "params locked: strategy %s at stage %s; recalibrator param write skipped",
                strategy_id, str(existing["stage"] or "").strip().lower(),
            )
            return
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "UPDATE strategies SET params = ?, updated_at = ?, type = COALESCE(type, ?)"
            " WHERE id = ?",
            (json.dumps(params), now, strategy_type, strategy_id),
        )


def check_and_recalibrate() -> dict[str, dict]:
    """Detect regime changes and validate parameter overlays.

    Returns:
        dict[str, dict]
            asset -> result metadata with status and details.
    """
    from axiom.strategies.backtest import backtest_strategy
    from axiom.scanner import STRATEGIES

    results: dict[str, dict] = {}
    for asset in TRACKED_ASSETS:
        current = detect_regime(asset)
        last_state = kv_get(f"last_regime:{asset}") or {}
        last_regime = str(last_state.get("regime", "")).strip()

        if last_regime == current.regime:
            results[asset] = _normalize_result(
                "no regime change", "skipped",
                {"current_regime": current.regime, "confidence": current.confidence},
            )
            continue

        log.info("Regime shift %s: %s -> %s", asset, last_regime or "unknown", current.regime)
        updated_count = 0
        tested_count = 0
        skipped_count = 0

        for strat_id, strat in STRATEGIES.items():
            if strat.get("asset", "").upper() != asset:
                continue

            strategy_type = strat.get("type")
            if not is_strategy_allowed(
                strategy_type,
                current.regime,
                confidence=current.confidence,
            ):
                skipped_count += 1
                log.info("Skipping %s on %s in %s regime", strat_id, asset, current.regime)
                continue

            base_params = dict(strat.get("params", {}))
            adjusted = get_adjusted_params(strategy_type, base_params, current.regime)
            if adjusted == base_params:
                continue

            try:
                tested_count += 1
                baseline = backtest_strategy(
                    strat_id,
                    asset,
                    strategy_type,
                    base_params,
                    bars=72,
                )
                candidate = backtest_strategy(
                    strat_id,
                    asset,
                    strategy_type,
                    adjusted,
                    bars=72,
                )

                base_metrics = baseline.get("metrics", {})
                cand_metrics = candidate.get("metrics", {})
                base_sharpe = float(base_metrics.get("sharpe", 0) or 0.0)
                cand_sharpe = float(cand_metrics.get("sharpe", 0) or 0.0)

                if cand_sharpe > base_sharpe:
                    STRATEGIES[strat_id]["params"] = adjusted
                    try:
                        _persist_strategy_params(strat_id, strategy_type, adjusted)
                    except Exception:
                        pass
                    updated_count += 1
                    log_activity(
                        "info",
                        "recalibrator",
                        f"{strat_id} on {asset}: sharpe {base_sharpe:.2f} -> {cand_sharpe:.2f}",
                    )
                    log.info("%s accepted for %s regime overlay", strat_id, current.regime)
                else:
                    log.info(
                        "Recalibration rejected for %s: %.2f -> %.2f",
                        strat_id,
                        base_sharpe,
                        cand_sharpe,
                    )
            except Exception as e:
                log.warning("Recalibration failed for %s: %s", strat_id, e)

        kv_set(f"last_regime:{asset}", {
            "regime": current.regime,
            "confidence": current.confidence,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "updated_count": updated_count,
        })

        results[asset] = _normalize_result(
            f"recalibrated from {last_regime or 'unknown'} to {current.regime}",
            "updated" if updated_count else "no-change",
            {
                "tested": tested_count,
                "skipped": skipped_count,
                "updated": updated_count,
                "confidence": current.confidence,
            },
        )

    return results
