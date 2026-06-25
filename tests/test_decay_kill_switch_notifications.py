"""B-34: the decay kill-switch must NOTIFY the operator when it fires —
including (especially) the BLOCKED case where the strategy is still live."""

from datetime import datetime, timezone
from unittest.mock import patch

from axiom.db import get_db, init_db
from axiom.monitoring import _emit_kill_switch_notification, run_decay_kill_switch

_EMIT_TARGET = "axiom.notifications.emit_notification"


def _seed_breaching_live_strategy(strategy_id: str = "S99001") -> None:
    """live_graduated strategy with a good baseline and 6 losing live trades —
    guaranteed to breach the default 30% degradation kill-switch."""
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        conn.execute(
            "INSERT INTO strategies (id, name, stage, metrics, created_at, updated_at) "
            "VALUES (?, ?, 'live_graduated', '{\"sharpe\": 2.0}', ?, ?)",
            (strategy_id, strategy_id, now, now),
        )
        for i in range(6):
            conn.execute(
                """INSERT INTO trades
                   (id, strategy, strategy_id, asset, direction, status,
                    pnl_pct, execution_type, closed_at)
                   VALUES (?, ?, ?, 'SOL', 'LONG', 'CLOSED', ?, 'live', ?)""",
                (f"T{strategy_id}{i}", strategy_id, strategy_id, -1.0 - i * 0.1, now),
            )


class TestEmitKillSwitchNotification:
    def test_archived_case_emits_risk_critical(self):
        with patch(_EMIT_TARGET) as mock_emit:
            _emit_kill_switch_notification(
                strategy_id="S00001",
                archived=True,
                degradation=0.55,
                kill_switch_pct=0.30,
                live_sharpe=0.9,
                baseline_sharpe=2.0,
                blocked_reason=None,
                reason_code=None,
                trigger_payload={"strategy_id": "S00001", "archived": True},
            )
        mock_emit.assert_called_once()
        args, kwargs = mock_emit.call_args
        assert args[0] == "risk_critical"
        assert kwargs["severity"] == "critical"
        assert kwargs["source"] == "decay_kill_switch"
        assert "ARCHIVED" in kwargs["title"]
        assert kwargs["dedupe_key"] == "decay_kill_switch:S00001:archived"

    def test_blocked_case_has_distinct_message_and_dedupe_key(self):
        with patch(_EMIT_TARGET) as mock_emit:
            _emit_kill_switch_notification(
                strategy_id="S00001",
                archived=False,
                degradation=0.55,
                kill_switch_pct=0.30,
                live_sharpe=0.9,
                baseline_sharpe=2.0,
                blocked_reason="canonical protection",
                reason_code="canonical",
                trigger_payload={"strategy_id": "S00001", "archived": False},
            )
        mock_emit.assert_called_once()
        args, kwargs = mock_emit.call_args
        assert args[0] == "risk_critical"
        assert kwargs["severity"] == "critical"
        assert "BLOCKED" in kwargs["title"]
        assert "still LIVE" in kwargs["title"]
        assert "STILL TRADING" in kwargs["summary"]
        assert "canonical protection" in kwargs["summary"]
        # Distinct key: a blocked alert may never be deduped against an
        # earlier successful-archive alert.
        assert kwargs["dedupe_key"] == "decay_kill_switch:S00001:blocked"

    def test_emit_failure_never_breaks_the_kill_switch(self):
        with patch(_EMIT_TARGET, side_effect=RuntimeError("discord down")):
            # Must not raise.
            _emit_kill_switch_notification(
                strategy_id="S00001",
                archived=True,
                degradation=0.55,
                kill_switch_pct=0.30,
                live_sharpe=0.9,
                baseline_sharpe=2.0,
                blocked_reason=None,
                reason_code=None,
                trigger_payload={},
            )


class TestRunDecayKillSwitchNotifies:
    def test_trigger_with_successful_archive_notifies(self, monkeypatch):
        init_db()
        _seed_breaching_live_strategy("S99001")
        monkeypatch.setattr(
            "axiom.brain.transition_stage",
            lambda **kwargs: {"to": "archived"},
        )

        with patch(_EMIT_TARGET) as mock_emit:
            summary = run_decay_kill_switch()

        assert summary["triggered_count"] == 1
        mock_emit.assert_called_once()
        args, kwargs = mock_emit.call_args
        assert args[0] == "risk_critical"
        assert "ARCHIVED" in kwargs["title"]
        assert kwargs["metadata"]["strategy_id"] == "S99001"

    def test_trigger_with_blocked_transition_notifies_blocked(self, monkeypatch):
        init_db()
        _seed_breaching_live_strategy("S99002")
        monkeypatch.setattr(
            "axiom.brain.transition_stage",
            lambda **kwargs: {
                "to": "live_graduated",
                "blocked_reason": "canonical strategy is protected",
                "reason_code": "canonical_protected",
            },
        )

        with patch(_EMIT_TARGET) as mock_emit:
            summary = run_decay_kill_switch()

        assert summary["triggered_count"] == 1
        assert summary["triggered"][0]["archived"] is False
        mock_emit.assert_called_once()
        args, kwargs = mock_emit.call_args
        assert args[0] == "risk_critical"
        assert "BLOCKED" in kwargs["title"]
        assert "STILL TRADING" in kwargs["summary"]
        assert kwargs["dedupe_key"] == "decay_kill_switch:S99002:blocked"

    def test_no_breach_no_notification(self, monkeypatch):
        init_db()
        with patch(_EMIT_TARGET) as mock_emit:
            summary = run_decay_kill_switch()
        assert summary["triggered_count"] == 0
        mock_emit.assert_not_called()
