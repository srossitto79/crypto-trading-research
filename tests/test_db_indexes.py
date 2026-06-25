from __future__ import annotations

import sqlite3

import axiom.config as cfg
import axiom.db as db_mod
from axiom.db import get_db, init_db


def test_status_indexes_exist_after_init(AXIOM_db):
    with get_db() as conn:
        trade_indexes = {row["name"] for row in conn.execute("PRAGMA index_list('trades')").fetchall()}
        task_indexes = {row["name"] for row in conn.execute("PRAGMA index_list('tasks')").fetchall()}
        agent_task_indexes = {row["name"] for row in conn.execute("PRAGMA index_list('agent_tasks')").fetchall()}
        scheduler_indexes = {row["name"] for row in conn.execute("PRAGMA index_list('scheduler_jobs')").fetchall()}

    assert "idx_trades_status" in trade_indexes
    assert "idx_tasks_status" in task_indexes
    assert "idx_tasks_type_status" in task_indexes
    assert "idx_agent_tasks_status" in agent_task_indexes
    assert "idx_agent_tasks_agent_status" in agent_task_indexes
    assert "idx_scheduler_jobs_last_status" in scheduler_indexes


def test_init_db_bootstraps_hypothesis_indexes_for_legacy_strategies_table(tmp_path):
    db_path = cfg.AXIOM_DB
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(
            """
            CREATE TABLE strategies (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                type TEXT,
                symbol TEXT,
                timeframe TEXT,
                params JSON,
                metrics JSON,
                verdict JSON,
                status TEXT DEFAULT 'quick_screen',
                owner TEXT DEFAULT 'brain',
                stage TEXT DEFAULT 'quick_screen',
                base_id INTEGER,
                display_id TEXT,
                audit_summary JSON,
                market_pot TEXT,
                last_prefix TEXT,
                notes TEXT,
                model TEXT,
                model_id TEXT,
                stage_changed_at TEXT,
                created_at TEXT,
                updated_at TEXT
            );
            """
        )
        conn.commit()
    finally:
        conn.close()

    db_mod.AXIOM_DB = db_path

    init_db()

    with get_db() as conn:
        strategy_columns = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info('strategies')").fetchall()
        }
        strategy_indexes = {
            str(row["name"])
            for row in conn.execute("PRAGMA index_list('strategies')").fetchall()
        }

    assert "hypothesis_id" in strategy_columns
    assert "idx_strategies_hypothesis_id" in strategy_indexes
