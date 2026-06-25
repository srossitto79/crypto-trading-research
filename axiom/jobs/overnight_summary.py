"""Overnight Pipeline Summary — morning brief job.

Gathers stats from the last N hours across all pipeline components
and emits a structured summary to the morning-brief Discord channel.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from axiom.db import get_db, kv_get

log = logging.getLogger("axiom.jobs.overnight_summary")

DEFAULT_LOOKBACK_HOURS = 12


def _iso_cutoff(lookback_hours: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=max(1, lookback_hours))).isoformat()


def _safe_int(value: object, default: int = 0) -> int:
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default


def _gather_scheduler_stats(cutoff_iso: str) -> dict[str, Any]:
    """Scheduler job execution stats."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT last_status, COUNT(*) as c FROM scheduler_jobs WHERE last_run_at >= ? GROUP BY last_status",
            (cutoff_iso,),
        ).fetchall()
    stats: dict[str, int] = {}
    for row in rows:
        stats[str(row["last_status"] or "unknown")] = int(row["c"])
    total = sum(stats.values())
    return {
        "total_ran": total,
        "errors": stats.get("error", 0),
        "skipped": stats.get("paused", 0) + stats.get("skipped", 0),
        "by_status": stats,
    }


def _gather_evolution_stats(cutoff_iso: str) -> dict[str, Any]:
    """Strategy evolution pipeline stats.

    Collapsed into two GROUP BY queries (created vs updated) instead of
    five COUNT(*) full scans. Without timestamp+stage indexes the original
    ran 5N row reads where N is the strategies table size; this is 2N.
    """
    counts = {"ideated": 0, "coded": 0, "tested": 0, "deployed": 0, "killed": 0}
    stage_to_bucket = {
        "ideation": "ideated",
        "backtesting": "coded",
        "paper": "tested",
        "deployed": "deployed",
        "graveyard": "killed",
        "killed": "killed",
    }
    with get_db() as conn:
        # ideation is gated by created_at; the rest by updated_at.
        ideation_row = conn.execute(
            "SELECT COUNT(*) c FROM strategies "
            "WHERE created_at >= ? AND COALESCE(stage, status) = 'ideation'",
            (cutoff_iso,),
        ).fetchone()
        counts["ideated"] = int(ideation_row["c"] or 0)

        rows = conn.execute(
            "SELECT COALESCE(stage, status) AS bucket, COUNT(*) AS c "
            "FROM strategies WHERE updated_at >= ? "
            "  AND COALESCE(stage, status) IN ('backtesting','paper','deployed','graveyard','killed') "
            "GROUP BY bucket",
            (cutoff_iso,),
        ).fetchall()
    for row in rows:
        bucket = stage_to_bucket.get(str(row["bucket"] or "").strip().lower())
        if bucket:
            counts[bucket] += int(row["c"] or 0)
    return counts


def _gather_lab_stats(cutoff_iso: str) -> dict[str, Any]:
    """Regime Lab job stats."""
    try:
        from axiom.lab_db import init_lab_db, get_lab_db, get_blacklist_summary

        init_lab_db()
        with get_lab_db() as conn:
            completed = conn.execute(
                "SELECT COUNT(*) as c FROM lab_job_queue WHERE state = 'SUCCEEDED' AND updated_at >= ?",
                (cutoff_iso,),
            ).fetchone()["c"]
            failed = conn.execute(
                "SELECT COUNT(*) as c FROM lab_job_queue WHERE state IN ('FAILED', 'DEADLETTER') AND updated_at >= ?",
                (cutoff_iso,),
            ).fetchone()["c"]
            matrix_jobs = conn.execute(
                "SELECT COUNT(*) as c FROM lab_job_queue WHERE job_type = 'backtests_matrix' AND updated_at >= ?",
                (cutoff_iso,),
            ).fetchone()["c"]
        blacklist = get_blacklist_summary()
    except Exception as exc:
        log.warning("Lab stats unavailable: %s", exc)
        return {"completed": 0, "failed": 0, "matrix_jobs": 0, "blacklist": {}}

    return {
        "completed": completed,
        "failed": failed,
        "matrix_jobs": matrix_jobs,
        "blacklist": blacklist,
    }


def _gather_resource_gate_stats() -> dict[str, Any]:
    """CPU resource gate stats from lab_meta."""
    try:
        from axiom.lab_db import get_lab_meta

        stats = get_lab_meta("resource_gate_stats", {})
        return stats if isinstance(stats, dict) else {}
    except Exception:
        return {}


def _gather_daemon_stats() -> dict[str, Any]:
    """Daemon health stats."""
    try:
        state = kv_get("daemon_state", {})
        if not isinstance(state, dict):
            return {"running": False}
        started_at = state.get("started_at")
        tick_count = _safe_int(state.get("tick_count"))
        return {
            "running": bool(state.get("running")),
            "started_at": started_at,
            "tick_count": tick_count,
        }
    except Exception:
        return {"running": False}


def _gather_trade_stats(cutoff_iso: str) -> dict[str, Any]:
    """Trade activity stats."""
    with get_db() as conn:
        entries = conn.execute(
            "SELECT COUNT(*) as c FROM trades WHERE opened_at >= ?",
            (cutoff_iso,),
        ).fetchone()["c"]
        exits = conn.execute(
            "SELECT COUNT(*) as c FROM trades WHERE closed_at >= ? AND status = 'CLOSED'",
            (cutoff_iso,),
        ).fetchone()["c"]
        total_pnl = conn.execute(
            "SELECT COALESCE(SUM(pnl_usd), 0) as total FROM trades WHERE closed_at >= ? AND status = 'CLOSED'",
            (cutoff_iso,),
        ).fetchone()["total"]
    return {
        "entries": entries,
        "exits": exits,
        "total_pnl_usd": round(float(total_pnl or 0), 2),
    }


def _gather_pipeline_health_stats() -> dict[str, Any]:
    """Pipeline throughput and self-healing stats."""
    stats: dict[str, Any] = {}
    try:
        from axiom.lab_db import get_lab_meta
        from axiom.lab_worker_service import PIPELINE_PROGRESS_META_KEY

        progress = get_lab_meta(PIPELINE_PROGRESS_META_KEY, {})
        if isinstance(progress, dict):
            stats["jobs_completed_last_hour"] = progress.get("jobs_completed_last_hour", 0)
            stats["last_job_completed_at"] = progress.get("last_job_completed_at", "unknown")
            stats["last_job_claimed_at"] = progress.get("last_job_claimed_at", "unknown")
    except Exception:
        pass

    try:
        consecutive_errors = int(kv_get("scheduler:consecutive_errors", "0") or 0)
        stats["scheduler_consecutive_errors"] = consecutive_errors
        stats["scheduler_last_tick"] = kv_get("scheduler:last_successful_tick", "unknown")
    except Exception:
        pass

    try:
        from axiom.lab_db import get_lab_meta
        gate_stats = get_lab_meta("resource_gate_stats", {})
        if isinstance(gate_stats, dict):
            stats["cpu_gate_total_skips"] = gate_stats.get("skips", 0)
    except Exception:
        pass

    return stats


def build_overnight_summary(lookback_hours: int = DEFAULT_LOOKBACK_HOURS) -> dict[str, Any]:
    """Gather all pipeline stats from the last N hours.

    The scheduler initializes the DB at app open, so a per-job ``init_db``
    here was redundant and added ~1s of schema-validation overhead to every
    invocation.
    """
    cutoff = _iso_cutoff(lookback_hours)
    return {
        "lookback_hours": lookback_hours,
        "cutoff_iso": cutoff,
        "scheduler": _gather_scheduler_stats(cutoff),
        "evolution": _gather_evolution_stats(cutoff),
        "lab": _gather_lab_stats(cutoff),
        "resource_gate": _gather_resource_gate_stats(),
        "daemon": _gather_daemon_stats(),
        "trades": _gather_trade_stats(cutoff),
        "pipeline_health": _gather_pipeline_health_stats(),
    }


def format_overnight_summary(stats: dict[str, Any]) -> str:
    """Format stats into a readable text block."""
    sched = stats.get("scheduler", {})
    evo = stats.get("evolution", {})
    lab = stats.get("lab", {})
    gate = stats.get("resource_gate", {})
    daemon = stats.get("daemon", {})
    trades = stats.get("trades", {})
    blacklist = lab.get("blacklist", {})

    lines = [
        f"Overnight Pipeline Summary (last {stats.get('lookback_hours', 12)}h)",
        "-" * 42,
        f"Scheduler: {sched.get('total_ran', 0)} jobs ran, {sched.get('errors', 0)} errors, {sched.get('skipped', 0)} skipped",
        f"Evolution: {evo.get('ideated', 0)} ideated \u2192 {evo.get('coded', 0)} coded \u2192 {evo.get('tested', 0)} tested \u2192 {evo.get('deployed', 0)} deployed, {evo.get('killed', 0)} killed",
        f"Regime Lab: {lab.get('completed', 0)} completed, {lab.get('failed', 0)} failed, {lab.get('matrix_jobs', 0)} matrix runs",
    ]

    if gate:
        lines.append(
            f"CPU Gate: {gate.get('skips', 0)} skips, {gate.get('reductions', 0)} reductions, {gate.get('full', 0)} full"
        )

    if blacklist:
        lines.append(
            f"Blacklist: {blacklist.get('total_blacklisted', 0)} strategies ({blacklist.get('blacklisted_last_24h', 0)} new)"
        )

    daemon_status = "online" if daemon.get("running") else "offline"
    lines.append(f"Daemon: {daemon_status}, {daemon.get('tick_count', 0)} ticks")
    lines.append(
        f"Trades: {trades.get('entries', 0)} entries, {trades.get('exits', 0)} exits, ${trades.get('total_pnl_usd', 0):.2f} P&L"
    )

    health = stats.get("pipeline_health", {})
    if health:
        sched_errs = health.get("scheduler_consecutive_errors", 0)
        total_skips = health.get("cpu_gate_total_skips", 0)
        last_completed = health.get("last_job_completed_at", "unknown")
        health_line = f"Pipeline Health: last completion {last_completed}"
        if sched_errs > 0:
            health_line += f", {sched_errs} scheduler errors"
        if total_skips > 0:
            health_line += f", {total_skips} CPU gate skips"
        lines.append(health_line)

    return "\n".join(lines)


def run_overnight_summary_job(lookback_hours: int = DEFAULT_LOOKBACK_HOURS) -> dict[str, Any]:
    """Build and emit the overnight summary notification."""
    stats = build_overnight_summary(lookback_hours)
    summary_text = format_overnight_summary(stats)

    try:
        from axiom.notifications import emit_notification

        emit_notification(
            "overnight_summary",
            severity="info",
            source="scheduler",
            title="Overnight Pipeline Summary",
            summary=summary_text,
            body=summary_text,
            channel_name="morning-brief",
            dedupe_key="overnight-summary-daily",
            metadata={"stats": stats},
        )
        log.info("Overnight summary emitted to morning-brief channel")
    except Exception as exc:
        log.error("Failed to emit overnight summary notification: %s", exc)

    return stats
