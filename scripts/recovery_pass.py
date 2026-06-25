import logging
from datetime import datetime, timedelta, timezone
from axiom.db import get_db, init_db
from axiom.brain import STAGE_TO_AGENT

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger("recovery_pass")

def run_recovery_pass():
    log.info("Starting Queue Recovery Pass...")
    init_db()
    
    with get_db() as conn:
        # 1. Cancel tasks stuck > 24 hours (fallback to 48 just to be safe if 'created_at' is used instead of started_at)
        cursor = conn.cursor()
        now = datetime.now(timezone.utc)
        stuck_threshold = (now - timedelta(hours=24)).isoformat()
        
        cursor.execute(
            """
            UPDATE agent_tasks 
            SET status = 'cancelled', error = 'Cancelled by recovery pass (stuck > 24h)'
            WHERE status IN ('pending', 'in_progress', 'blocked') 
            AND created_at < ?
            """,
            (stuck_threshold,)
        )
        stuck_cancelled = cursor.rowcount
        log.info(f"Cancelled {stuck_cancelled} zombie tasks older than 24 hours.")

        # 2. Terminate duplicate active tasks (same agent, strategy, and title)
        # Keep the most recently created one
        cursor.execute(
            """
            SELECT agent_id, strategy_id, type, COUNT(*) as cnt
            FROM agent_tasks
            WHERE status IN ('pending', 'in_progress', 'blocked')
            GROUP BY agent_id, strategy_id, type
            HAVING cnt > 1
            """
        )
        duplicates = cursor.fetchall()
        dupes_cancelled = 0
        for row in duplicates:
            # Get all but the latest
            cursor.execute(
                """
                SELECT id FROM agent_tasks
                WHERE agent_id = ? AND strategy_id = ? AND type = ? AND status IN ('pending', 'in_progress', 'blocked')
                ORDER BY created_at DESC
                """,
                (row["agent_id"], row["strategy_id"], row["type"])
            )
            tasks = cursor.fetchall()
            old_task_ids = [t["id"] for t in tasks[1:]] # keep tasks[0]
            if old_task_ids:
                placeholders = ",".join("?" for _ in old_task_ids)
                cursor.execute(
                    f"UPDATE agent_tasks SET status = 'cancelled', error = 'Cancelled by recovery pass (duplicate)' WHERE id IN ({placeholders})",
                    old_task_ids
                )
                dupes_cancelled += len(old_task_ids)
        
        log.info(f"Terminated {dupes_cancelled} duplicate active tasks.")

        # 3. Align strategy owner to stage
        owner_realigned = 0
        for stage, expected_owner in STAGE_TO_AGENT.items():
            if expected_owner:
                cursor.execute(
                    """
                    UPDATE strategies
                    SET owner = ?
                    WHERE stage = ? AND (owner != ? OR owner IS NULL)
                    """,
                    (expected_owner, stage, expected_owner)
                )
                owner_realigned += cursor.rowcount
        log.info(f"Realigned ownership for {owner_realigned} strategies.")

        # 4. Purge orphaned approvals
        cursor.execute(
            """
            DELETE FROM approvals 
            WHERE target_type = 'strategy' 
            AND target_id NOT IN (SELECT display_id FROM strategies)
            """
        )
        orphaned_approvals = cursor.rowcount
        log.info(f"Purged {orphaned_approvals} orphaned approvals.")

        # 5. Purge orphaned slippage records (where strategy no longer exists)
        cursor.execute(
            """
            DELETE FROM trade_slippage_audit
            WHERE strategy_id NOT IN (SELECT id FROM strategies)
            AND strategy_id IS NOT NULL AND strategy_id != ''
            """
        )
        orphaned_slippage = cursor.rowcount
        log.info(f"Purged {orphaned_slippage} orphaned slippage audit records.")

    log.info("Queue Recovery Pass complete. The system is unblocked.")

if __name__ == "__main__":
    run_recovery_pass()
