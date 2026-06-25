from __future__ import annotations

import axiom.api_core as core


def test_get_settings_includes_research_settings_defaults(AXIOM_db):
    settings = core.get_settings()

    research_settings = settings.get("research_settings")
    assert isinstance(research_settings, dict)
    assert research_settings["external_benchmarking_enabled"] is True
    assert research_settings["memory_modes"]["exploration"]["inspiration_memory"] == "optional"
    assert "book" in research_settings["allowed_external_source_types"]


def test_put_research_settings_merges_nested_values(AXIOM_db):
    updated = core.put_settings_section(
        "research",
        {
            "research_settings": {
                "external_benchmarking_enabled": False,
                "lane_weights": {
                    "benchmarking": 0.4,
                },
                "spawn_limits": {
                    "per_run": 3,
                },
                "memory_modes": {
                    "exploration": {
                        "inspiration_memory": "bounded",
                    },
                },
            },
        },
    )

    research_settings = updated["research_settings"]
    assert research_settings["external_benchmarking_enabled"] is False
    assert research_settings["lane_weights"]["exploration"] == 0.3
    assert research_settings["lane_weights"]["benchmarking"] == 0.4
    assert research_settings["spawn_limits"]["per_run"] == 3
    assert research_settings["memory_modes"]["exploration"]["constraint_memory"] is True
    assert research_settings["memory_modes"]["exploration"]["inspiration_memory"] == "bounded"

    persisted = core.get_settings()
    assert persisted["research_settings"]["spawn_limits"]["per_run"] == 3
