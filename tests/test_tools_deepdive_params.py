import json
import pytest
from axiom.db import get_db


@pytest.fixture
def tmp_strategy(AXIOM_db):
    with get_db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO strategies (id, name, params) VALUES (?, ?, ?)",
            ("S77001", "test", json.dumps({"rsi_period": 14, "rsi_threshold": 30})),
        )
        conn.commit()
    return "S77001"


def test_update_existing_params(tmp_strategy):
    from axiom.agents.tools_deepdive import _update_default_params, set_deepdive_strategy
    set_deepdive_strategy(tmp_strategy)
    _update_default_params(params={"rsi_period": 21}, rationale="longer lookback", thread_id="dd_t")
    with get_db() as conn:
        row = conn.execute(
            "SELECT params FROM strategies WHERE id = ?", (tmp_strategy,)
        ).fetchone()
    merged = json.loads(row[0])
    assert merged["rsi_period"] == 21
    assert merged["rsi_threshold"] == 30  # unchanged


def test_update_unknown_key_rejected(tmp_strategy):
    from axiom.agents.tools_deepdive import _update_default_params, set_deepdive_strategy
    set_deepdive_strategy(tmp_strategy)
    with pytest.raises(ValueError, match="unknown param"):
        _update_default_params(params={"made_up_key": 1}, rationale="x", thread_id="dd_t")


def test_update_logs_to_activity(tmp_strategy):
    from axiom.agents.tools_deepdive import _update_default_params, set_deepdive_strategy
    set_deepdive_strategy(tmp_strategy)
    _update_default_params(params={"rsi_period": 21}, rationale="r", thread_id="dd_p")
    with get_db() as conn:
        rows = conn.execute(
            "SELECT source, message FROM activity_log "
            "WHERE source = 'deepdive_agent:dd_p' ORDER BY id DESC LIMIT 1"
        ).fetchall()
    assert rows
