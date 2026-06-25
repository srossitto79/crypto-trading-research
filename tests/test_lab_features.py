from __future__ import annotations

from datetime import datetime, timezone


def _insert_strategy(conn, strategy_id: str, stage: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO strategies
            (id, name, type, symbol, timeframe, params, metrics, status, stage, created_at, updated_at, stage_changed_at)
        VALUES
            (?, ?, 'ema_cross', 'BTC/USDT', '1h', '{}', '{}', ?, ?, ?, ?, ?)
        """,
        (strategy_id, strategy_id, stage, stage, now, now, now),
    )


def test_paper_wip_default_allows_unattended_rotation(AXIOM_db):
    from axiom.db import get_db
    from axiom.lab_features import check_stage_wip_capacity

    with get_db() as conn:
        for idx in range(10):
            _insert_strategy(conn, f"S-PAPER-{idx:02d}", "paper")

    has_capacity, current, cap, reason = check_stage_wip_capacity("paper")

    assert has_capacity is True
    assert current == 10
    assert cap == 20
    assert "10/20" in reason


def test_paper_wip_override_can_still_enforce_tighter_cap(AXIOM_db):
    from axiom.db import get_db, kv_set
    from axiom.lab_features import check_stage_wip_capacity

    kv_set("pipeline:wip_cap:paper", 2)
    with get_db() as conn:
        _insert_strategy(conn, "S-PAPER-A", "paper")
        _insert_strategy(conn, "S-PAPER-B", "paper")

    has_capacity, current, cap, reason = check_stage_wip_capacity("paper")

    assert has_capacity is False
    assert current == 2
    assert cap == 2
    assert "WIP cap reached" in reason


def test_paper_wip_override_can_be_unlimited(AXIOM_db):
    from axiom.db import get_db, kv_set
    from axiom.lab_features import check_stage_wip_capacity

    kv_set("pipeline:wip_cap:paper", "unlimited")
    with get_db() as conn:
        for idx in range(25):
            _insert_strategy(conn, f"S-PAPER-UNLIMITED-{idx:02d}", "paper")

    has_capacity, current, cap, reason = check_stage_wip_capacity("paper")

    assert has_capacity is True
    assert current == 25
    assert cap is None
    assert "No WIP cap" in reason


def test_pipeline_settings_can_set_paper_wip_unlimited(AXIOM_db):
    from axiom.api_core import PipelineSettingsUpdateBody, get_settings, put_pipeline_settings
    from axiom.db import kv_get
    from axiom.lab_features import check_stage_wip_capacity

    put_pipeline_settings(
        PipelineSettingsUpdateBody(
            updates={"paper_wip_cap_mode": "unlimited", "paper_wip_cap": 20},
            actor="test",
        )
    )

    has_capacity, current, cap, reason = check_stage_wip_capacity("paper")
    settings = get_settings()

    assert has_capacity is True
    assert current == 0
    assert cap is None
    assert "No WIP cap" in reason
    assert kv_get("pipeline:wip_cap:paper") == "unlimited"
    assert settings["paper_wip_cap_mode"] == "unlimited"
    assert settings["paper_wip_cap"] == 20


def test_pipeline_settings_can_set_paper_wip_cap(AXIOM_db):
    from axiom.api_core import PipelineSettingsUpdateBody, put_pipeline_settings
    from axiom.db import get_db, kv_get
    from axiom.lab_features import check_stage_wip_capacity

    put_pipeline_settings(
        PipelineSettingsUpdateBody(
            updates={"paper_wip_cap_mode": "capped", "paper_wip_cap": 3},
            actor="test",
        )
    )
    with get_db() as conn:
        for idx in range(3):
            _insert_strategy(conn, f"S-PAPER-CAPPED-{idx:02d}", "paper")

    has_capacity, current, cap, reason = check_stage_wip_capacity("paper")

    assert has_capacity is False
    assert current == 3
    assert cap == 3
    assert kv_get("pipeline:wip_cap:paper") == 3
    assert "WIP cap reached" in reason


def test_pipeline_settings_can_set_graveyard_strategy_limit_unlimited(AXIOM_db):
    from axiom.api_core import (
        PipelineSettingsUpdateBody,
        configured_graveyard_strategy_limit,
        get_settings,
        put_pipeline_settings,
        resolve_strategy_query_limit,
    )

    put_pipeline_settings(
        PipelineSettingsUpdateBody(
            updates={"graveyard_strategy_limit_mode": "unlimited", "graveyard_strategy_limit": 500},
            actor="test",
        )
    )
    settings = get_settings()

    assert configured_graveyard_strategy_limit() is None
    assert resolve_strategy_query_limit("archived", None) is None
    assert resolve_strategy_query_limit("archived", 1000) == 1000
    assert settings["graveyard_strategy_limit_mode"] == "unlimited"
    assert settings["graveyard_strategy_limit"] == 500


def test_strategy_query_limit_honors_graveyard_strategy_cap(AXIOM_db):
    from axiom.api_core import (
        PipelineSettingsUpdateBody,
        configured_graveyard_strategy_limit,
        put_pipeline_settings,
        resolve_strategy_query_limit,
    )

    put_pipeline_settings(
        PipelineSettingsUpdateBody(
            updates={"graveyard_strategy_limit_mode": "capped", "graveyard_strategy_limit": 3},
            actor="test",
        )
    )

    assert configured_graveyard_strategy_limit() == 3
    assert resolve_strategy_query_limit("archived", None) == 3
    assert resolve_strategy_query_limit("archived", 1000) == 3
    assert resolve_strategy_query_limit("archived", 1000, offset=2) == 1
    assert resolve_strategy_query_limit("archived", 1000, offset=3) == 0
    assert resolve_strategy_query_limit("quick_screen", None) == 500
