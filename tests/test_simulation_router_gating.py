from __future__ import annotations

from axiom.routers import simulation as simulation_router


def test_simulation_api_enabled_defaults_false(monkeypatch):
    monkeypatch.delenv("AXIOM_ENABLE_SIMULATION_API", raising=False)

    assert simulation_router.simulation_api_enabled() is False


def test_simulation_api_enabled_accepts_truthy_flag(monkeypatch):
    monkeypatch.setenv("AXIOM_ENABLE_SIMULATION_API", "true")

    assert simulation_router.simulation_api_enabled() is True
