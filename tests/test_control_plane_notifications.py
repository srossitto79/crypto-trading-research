from __future__ import annotations

import pytest
from fastapi import HTTPException

from axiom.control_plane import notifications as control_plane_notifications
from axiom.control_plane.models import NotificationPreferencesBody
from axiom.notifications import emit_notification


def test_get_notifications_list_includes_items_stats_and_preferences(AXIOM_db, monkeypatch):
    monkeypatch.setattr("axiom.bot.send_sync", lambda *args, **kwargs: True)

    item = emit_notification(
        "agent_task_completed",
        source="agent:strategy-developer",
        title="Strategy Developer: finished review",
        summary="Generated three candidate fixes",
        metadata={"task_id": "T01001"},
    )

    payload = control_plane_notifications.get_notifications_list(limit=5)

    assert "items" in payload
    assert "stats" in payload
    assert "preferences" in payload
    assert payload["items"][0]["id"] == item["id"]
    assert payload["items"][0]["group_key"] == item["group_key"]


def test_get_notifications_grouped_includes_groups_stats_and_preferences(AXIOM_db, monkeypatch):
    monkeypatch.setattr("axiom.bot.send_sync", lambda *args, **kwargs: True)

    latest = emit_notification(
        "system_degraded",
        source="daemon",
        severity="critical",
        title="Scanner execution stale",
        summary="Last execution scan 31m ago",
    )

    payload = control_plane_notifications.get_notifications_grouped(limit=5)

    assert "groups" in payload
    assert "pagination" in payload
    assert "stats" in payload
    assert "preferences" in payload
    assert payload["groups"][0]["event_type"] == latest["event_type"]
    assert payload["groups"][0]["group_key"] == latest["group_key"]
    assert payload["groups"][0]["latest_item"]["id"] == latest["id"]
    assert payload["pagination"] == {
        "limit": 5,
        "has_more": False,
        "next_cursor": None,
    }


def test_get_notifications_list_passes_group_key_filter(AXIOM_db, monkeypatch):
    monkeypatch.setattr("axiom.bot.send_sync", lambda *args, **kwargs: True)

    first = emit_notification(
        "system_degraded",
        source="daemon",
        title="Scanner execution stale",
        summary="Last execution scan 31m ago",
        dedupe_key="runtime:scanner-stale",
    )
    emit_notification(
        "system_degraded",
        source="queue-worker",
        title="Queue worker stalled",
        summary="Queue depth is increasing.",
        dedupe_key="runtime:queue-stalled",
    )

    payload = control_plane_notifications.get_notifications_list(limit=5, group_key="runtime:scanner-stale")

    assert [item["id"] for item in payload["items"]] == [first["id"]]


def test_post_notification_acknowledge_raises_404_for_missing_notification(AXIOM_db):
    with pytest.raises(HTTPException) as exc_info:
        control_plane_notifications.post_notification_acknowledge(999_999)

    assert exc_info.value.status_code == 404


def test_put_notifications_preferences_round_trips(AXIOM_db):
    body = NotificationPreferencesBody(
        updates={
            "agent_completion_to_discord": True,
            "response_channels": ["chat"],
        }
    )

    updated = control_plane_notifications.put_notifications_preferences(body)

    assert updated["agent_completion_to_discord"] is True
    assert updated["response_channels"] == ["chat"]
    assert control_plane_notifications.get_notifications_preferences()["response_channels"] == ["chat"]
