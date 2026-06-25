from axiom import reporter


def _capture_notification(monkeypatch):
    captured = {}

    def fake_emit_notification(event_type, **kwargs):
        captured["event_type"] = event_type
        captured.update(kwargs)
        return captured

    monkeypatch.setattr(reporter, "emit_notification", fake_emit_notification)
    return captured


def test_emit_agent_task_notification_keeps_post_mortem_report_completed(monkeypatch):
    captured = _capture_notification(monkeypatch)

    reporter._emit_agent_task_notification(
        "quant-researcher",
        "Post-Mortem Review Complete",
        (
            "Summary\n"
            "What worked: entries were disciplined.\n"
            "What failed: stop placement lagged.\n"
            "Next steps: tighten exits."
        ),
        task_id=101,
        task_display_id="T00101",
        task_type="post_mortem",
    )

    assert captured["event_type"] == "agent_task_completed"
    assert captured["severity"] == "info"
    assert captured["metadata"]["task_id"] == "101"
    assert captured["metadata"]["task_display_id"] == "T00101"
    assert captured["metadata"]["task_type"] == "post_mortem"


def test_emit_agent_task_notification_uses_failed_event_for_failure_title(monkeypatch):
    captured = _capture_notification(monkeypatch)

    reporter._emit_agent_task_notification(
        "strategy-developer",
        "Task failed: review settings sync",
        "The fix notes mention what failed, but the title should drive classification here.",
    )

    assert captured["event_type"] == "agent_task_failed"
    assert captured["severity"] == "warn"


def test_emit_agent_task_notification_uses_failed_event_for_failed_metadata(monkeypatch):
    captured = _capture_notification(monkeypatch)

    reporter._emit_agent_task_notification(
        "strategy-developer",
        "Audit review complete",
        "What failed: one experiment branch underperformed, but this is still narrative text.",
        metadata={"task_status": "failed"},
    )

    assert captured["event_type"] == "agent_task_failed"
    assert captured["severity"] == "warn"


def test_emit_agent_task_notification_does_not_infer_risk_alert_from_audit_prose(monkeypatch):
    captured = _capture_notification(monkeypatch)

    reporter._emit_agent_task_notification(
        "risk-manager",
        "Risk audit review complete",
        (
            "Reviewed the kill switch incident.\n"
            "What failed: escalation took too long.\n"
            "This report is a post-mortem, not a live alert."
        ),
        task_type="risk_audit",
    )

    assert captured["event_type"] == "agent_task_completed"
    assert captured["severity"] == "info"


def test_emit_agent_task_notification_uses_risk_critical_for_risk_alert_title(monkeypatch):
    captured = _capture_notification(monkeypatch)

    reporter._emit_agent_task_notification(
        "risk-manager",
        "Kill switch triggered",
        "Drawdown breached the configured threshold and trading is halted.",
    )

    assert captured["event_type"] == "risk_critical"
    assert captured["severity"] == "critical"
