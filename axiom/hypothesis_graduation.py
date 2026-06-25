"""Phase 6: graduation flow.

When a hypothesis reaches verdict='proven', graduate it:
  1. manager_state: active → graduated
  2. graduated_at: now()
  3. next_revisit_at: now() + revisit_interval_days
  4. flag the best child per (asset, timeframe) cell as canonical=TRUE
     and clear the rest

Frees a slot in the active-pool cap. Also ensures the agent prompt for any
later revisit can show coverage.

Idempotent: re-graduating a hypothesis updates next_revisit_at but does not
re-flag canonicals (the existing flags are already the per-cell best).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from axiom.db import get_db, kv_get
from axiom.research_contract import get_hypothesis_discipline_settings

log = logging.getLogger(__name__)

# Strategy stages that qualify a child for canonical consideration.
_QUALIFYING_STAGES = frozenset({"gauntlet", "paper", "live_graduated"})
# Stricter set when `canonical_requires_forward_proof` is enabled: only a child
# that has actually reached forward (paper/live) trading may be canonical.
_FORWARD_PROVEN_STAGES = frozenset({"paper", "live_graduated"})


def graduate_hypothesis(hypothesis_id: str) -> dict[str, Any]:
    """Transition a hypothesis to manager_state='graduated' and flag canonicals.

    Returns a dict: {hypothesis_id, graduated_at, next_revisit_at,
    canonical_strategy_ids, demoted_strategy_ids, was_already_graduated}.
    """
    discipline = get_hypothesis_discipline_settings()
    interval_days = int(discipline["revisit_interval_days"])
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    next_revisit = (now + timedelta(days=interval_days)).isoformat()

    # Top-level settings (read OUTSIDE the write txn; best-effort, default off so the
    # current behaviour is byte-identical unless an operator opts in).
    settings = kv_get("axiom:settings")
    settings = settings if isinstance(settings, dict) else {}
    require_forward_proof = bool(settings.get("canonical_requires_forward_proof", False))
    auto_deploy = bool(settings.get("canonical_auto_deploy_enabled", False))

    canonical_ids: list[str] = []
    demoted_ids: list[str] = []
    was_already_graduated = False

    with get_db() as conn:
        row = conn.execute(
            "SELECT manager_state, status FROM hypotheses WHERE id = ?",
            (hypothesis_id,),
        ).fetchone()
        if not row:
            raise ValueError(f"unknown hypothesis_id: {hypothesis_id}")

        was_already_graduated = (str(row["manager_state"] or "").strip() == "graduated")

        conn.execute(
            """
            UPDATE hypotheses
            SET manager_state = 'graduated',
                status = 'proven',
                protection_status = 'protected',
                protected_at = COALESCE(protected_at, ?),
                protected_by = COALESCE(protected_by, 'graduation'),
                graduated_at = COALESCE(graduated_at, ?),
                next_revisit_at = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (now_iso, now_iso, next_revisit, now_iso, hypothesis_id),
        )

        if not was_already_graduated:
            canonical_ids, demoted_ids = _flag_canonicals(
                conn, hypothesis_id, require_forward_proof=require_forward_proof
            )
        conn.commit()

    # Optionally drive newly-chosen canonical winners toward paper THROUGH the
    # gauntlet gate (never a direct transition — the robustness/required-test floor
    # still applies). Default off; best-effort and never raises (the stranded-proven
    # sweep relies on graduation staying idempotent).
    if auto_deploy and canonical_ids and not was_already_graduated:
        _enqueue_canonical_paper_promotion(canonical_ids)

    log.info(
        "hypothesis.graduated id=%s canonicals=%d demoted=%d already_graduated=%s",
        hypothesis_id, len(canonical_ids), len(demoted_ids), was_already_graduated,
    )
    return {
        "hypothesis_id": hypothesis_id,
        "graduated_at": now_iso,
        "next_revisit_at": next_revisit,
        "canonical_strategy_ids": canonical_ids,
        "demoted_strategy_ids": demoted_ids,
        "was_already_graduated": was_already_graduated,
    }


def _flag_canonicals(
    conn, hypothesis_id: str, *, require_forward_proof: bool = False
) -> tuple[list[str], list[str]]:
    """Pick best per (asset, timeframe) and set canonical=TRUE; clear others.

    "Best" is the qualifying child (stage in {gauntlet, paper, live_graduated}, or
    only {paper, live_graduated} when ``require_forward_proof``) with the highest
    sharpe_ratio in its latest backtest_results row. Children without backtest
    results are eligible only if no other child in their cell has metrics.
    """
    qualifying_stages = _FORWARD_PROVEN_STAGES if require_forward_proof else _QUALIFYING_STAGES
    rows = conn.execute(
        """
        SELECT s.id, s.symbol, s.timeframe, s.stage,
               (SELECT metrics_json FROM backtest_results r
                WHERE r.strategy_id = s.id AND r.deleted_at IS NULL
                ORDER BY r.created_at DESC LIMIT 1) AS latest_metrics
        FROM strategies s
        WHERE s.hypothesis_id = ?
          AND s.stage NOT IN ('archived', 'rejected')
        """,
        (hypothesis_id,),
    ).fetchall()

    qualifying_by_cell: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        stage = str(row["stage"] or "").strip().lower()
        if stage not in qualifying_stages:
            continue
        cell = (str(row["symbol"] or ""), str(row["timeframe"] or ""))
        sharpe = _extract_sharpe(row["latest_metrics"])
        qualifying_by_cell.setdefault(cell, []).append({
            "strategy_id": str(row["id"]),
            "sharpe": sharpe,
        })

    chosen: set[str] = set()
    for cell, candidates in qualifying_by_cell.items():
        # Prefer the candidate with the highest sharpe; None < any number
        candidates.sort(
            key=lambda c: (c["sharpe"] is not None, c["sharpe"] or 0.0),
            reverse=True,
        )
        chosen.add(candidates[0]["strategy_id"])

    # Apply: chosen → canonical=1, all others under this hypothesis → canonical=0
    conn.execute(
        "UPDATE strategies SET canonical = 0 WHERE hypothesis_id = ?",
        (hypothesis_id,),
    )
    if chosen:
        placeholders = ",".join("?" * len(chosen))
        conn.execute(
            f"UPDATE strategies SET canonical = 1 WHERE id IN ({placeholders})",
            tuple(chosen),
        )

    all_ids = {str(r["id"]) for r in rows}
    demoted = list(all_ids - chosen)
    return list(chosen), demoted


def _enqueue_canonical_paper_promotion(canonical_ids: list[str]) -> None:
    """Ensure each gauntlet-stage canonical has a gauntlet workflow to drive it to
    paper THROUGH the paper-promotion gate (robustness/required-test floor enforced).

    This does NOT call transition_stage directly — that would be a back-door past the
    gate. It only guarantees a workflow exists (idempotent create_or_get_workflow);
    the periodic gauntlet tick then advances it and run_paper_promotion_gate decides.
    Best-effort: any failure is logged and swallowed so graduation stays idempotent.
    """
    try:
        with get_db() as conn:
            rows = conn.execute(
                "SELECT id, stage FROM strategies WHERE id IN ({})".format(
                    ",".join("?" * len(canonical_ids))
                ),
                tuple(canonical_ids),
            ).fetchall()
        gauntlet_ids = [
            str(r["id"]) for r in rows
            if str(r["stage"] or "").strip().lower() == "gauntlet"
        ]
        if not gauntlet_ids:
            return
        from axiom.gauntlet.settings import build_settings_snapshot
        from axiom.gauntlet.store import create_or_get_workflow

        snapshot = build_settings_snapshot()
        for strategy_id in gauntlet_ids:
            try:
                create_or_get_workflow(
                    strategy_id=strategy_id,
                    created_by="canonical_auto_deploy",
                    settings_snapshot=snapshot,
                )
            except Exception:
                log.exception("canonical auto-deploy: failed to ensure workflow for %s", strategy_id)
        log.info("canonical auto-deploy: ensured paper-promotion workflow for %d canonical(s)", len(gauntlet_ids))
    except Exception:
        log.exception("canonical auto-deploy: enqueue pass failed")


def _extract_sharpe(blob: Any) -> float | None:
    if blob is None:
        return None
    try:
        import json
        data = json.loads(blob) if isinstance(blob, str) else blob
        if not isinstance(data, dict):
            return None
        val = data.get("sharpe_ratio") or data.get("sharpe")
        if val is None:
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def is_canonical(strategy_id: str) -> bool:
    """Cleanup-protection helper: returns True if the strategy is canonical."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT canonical FROM strategies WHERE id = ?",
            (str(strategy_id),),
        ).fetchone()
    return bool(row and row["canonical"])
