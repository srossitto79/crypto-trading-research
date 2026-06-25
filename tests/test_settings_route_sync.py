"""Execution-mode route behavior for unified Axiom API."""

from axiom.api import ExecutionModeBody, post_execution_mode
from axiom.config import get_execution_mode, set_execution_mode


def test_update_execution_mode_paper_via_route(AXIOM_db):
    set_execution_mode("paper")
    response = post_execution_mode(ExecutionModeBody(mode="paper", confirm=True))

    assert response["ok"] is True
    assert response["mode"] == "paper"
    assert get_execution_mode() == "paper"


def test_update_execution_mode_live_rejected_via_route(AXIOM_db):
    # Live/mainnet is not a supported feature — the route rejects it cleanly.
    response = post_execution_mode(ExecutionModeBody(mode="live", confirm=True))
    assert response["ok"] is False
    assert "not a supported feature" in response["error"]


def test_update_execution_mode_requires_confirmation(AXIOM_db):
    response = post_execution_mode(ExecutionModeBody(mode="live", confirm=False))
    assert response["ok"] is False
    assert response["error"] == "Confirmation required"
