from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI
from fastapi.testclient import TestClient

from axiom.api_domains import memory as memory_domain
from axiom.routers.memory import router as memory_router


def _pin_workspace(monkeypatch) -> Path:
    workspace_dir = Path(memory_domain.cfg.WORKSPACE_DIR)
    monkeypatch.setattr(memory_domain.cfg, "LEGACY_WORKSPACE_DIR", workspace_dir, raising=False)
    return workspace_dir


NARRATIVES_SOURCE = memory_domain.NARRATIVES_SOURCE


def test_workspace_memory_items_are_sectioned_and_deterministic(AXIOM_db, monkeypatch):
    workspace_dir = _pin_workspace(monkeypatch)
    (workspace_dir / "memory").mkdir(parents=True, exist_ok=True)
    (workspace_dir / "agents" / "brain" / "memory").mkdir(parents=True, exist_ok=True)

    (workspace_dir / "memory" / "MEMORY.md").write_text(
        "# Alpha Setup\nThis is the main setup.\n\n## Risk Note\nStops matter.\n",
        encoding="utf-8",
    )
    (workspace_dir / "agents" / "brain" / "memory" / "2026-03-10.md").write_text(
        "# Daily Log\nReviewed drawdown regime.\n",
        encoding="utf-8",
    )

    items = memory_domain._workspace_items()
    source_ids = {item["source_id"] for item in items}

    assert "memory/MEMORY.md#alpha-setup" in source_ids
    assert "memory/MEMORY.md#risk-note" in source_ids
    assert "agents/brain/memory/2026-03-10.md" in source_ids

    brain_item = next(item for item in items if item["source_id"] == "agents/brain/memory/2026-03-10.md")
    assert brain_item["agent_id"] == "brain"
    assert "daily" in brain_item["tags"]
    assert brain_item["title"] == "brain Daily Log 2026-03-10"


def test_daily_agent_logs_are_compacted_with_capped_signal_sections(AXIOM_db, monkeypatch):
    workspace_dir = _pin_workspace(monkeypatch)
    daily_dir = workspace_dir / "agents" / "strategy-developer" / "memory"
    daily_dir.mkdir(parents=True, exist_ok=True)

    noisy_sections = "\n".join(
        f"## Next Steps {idx}\nRoutine generated status update {idx}.\n"
        for idx in range(60)
    )
    signal_sections = "\n".join(
        f"## Failure S{10000 + idx}\nStrategy S{10000 + idx} failed a risk gate.\n"
        for idx in range(memory_domain.MAX_DAILY_AGENT_SECTIONS + 5)
    )
    (daily_dir / "2026-04-13.md").write_text(
        f"# Daily Log\nGenerated chatter.\n\n{noisy_sections}\n{signal_sections}",
        encoding="utf-8",
    )

    items = [
        item
        for item in memory_domain._workspace_items()
        if item["source_id"].startswith("agents/strategy-developer/memory/2026-04-13.md")
    ]

    assert len(items) == memory_domain.MAX_DAILY_AGENT_SECTIONS + 1
    assert any(item["source_id"] == "agents/strategy-developer/memory/2026-04-13.md" for item in items)
    assert not any("next-steps" in item["source_id"] for item in items)
    assert sum("failure" in item["source_id"] for item in items) == memory_domain.MAX_DAILY_AGENT_SECTIONS


def test_annotation_overlay_precedence():
    item = {
        "source": "workspace",
        "source_kind": "workspace_markdown",
        "source_id": "memory/MEMORY.md#risk-note",
        "title": "Risk Note",
        "tags": ["risk", "daily"],
        "tier": None,
        "pinned": False,
        "hidden": False,
        "note": None,
    }
    annotation = {
        "title_override": "Canon Risk Note",
        "tags": ["canon", "risk"],
        "tier": "canon",
        "pinned": True,
        "hidden": False,
        "note": "Keep this in the permanent shelf.",
        "updated_at": "2026-03-10T00:00:00+00:00",
    }

    applied = memory_domain._apply_annotation(item, annotation)

    assert applied["title"] == "Canon Risk Note"
    assert applied["pinned"] is True
    assert applied["tier"] == "canon"
    assert applied["note"] == "Keep this in the permanent shelf."
    assert set(applied["tags"]) == {"risk", "daily", "canon"}


def test_search_memory_records_handles_chroma_degraded(AXIOM_db, monkeypatch):
    workspace_dir = _pin_workspace(monkeypatch)
    (workspace_dir / "memory").mkdir(parents=True, exist_ok=True)
    (workspace_dir / "memory" / "drawdown_notes.md").write_text(
        "# Drawdown Regime\nRespect the drawdown regime and cut weak ideas.\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(memory_domain, "_run_chroma_subprocess", lambda payload, timeout=60: {"ok": False, "error": "chroma down"})

    payload = asyncio.run(
        memory_domain.search_memory_records(
            memory_domain.MemorySearchRequest(
                query="drawdown regime",
                sources=["workspace", "chroma"],
            )
        )
    )

    assert payload["results"]
    assert payload["results"][0]["source"] == "workspace"
    chroma_health = next(entry for entry in payload["source_health"] if entry["source"] == "chroma")
    assert chroma_health["healthy"] is False
    assert chroma_health["status"] == "degraded"


def test_memory_overview_reports_narrative_browse_mode(AXIOM_db, monkeypatch):
    workspace_dir = _pin_workspace(monkeypatch)
    (workspace_dir / "memory").mkdir(parents=True, exist_ok=True)
    (workspace_dir / "memory" / "MEMORY.md").write_text(
        "# Operator Notes\nWorkspace memory still loads normally.\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(memory_domain, "_run_chroma_subprocess", lambda payload, timeout=60: {"ok": True, "collections": {}})
    monkeypatch.setattr(memory_domain, "_browse_narrative_items_sync", lambda limit: [])

    payload = memory_domain.get_memory_overview()
    narrative_health = next(entry for entry in payload["source_health"] if entry["source"] == NARRATIVES_SOURCE)

    assert narrative_health["configured"] is True
    assert narrative_health["count"] == 0


def test_memory_overview_drops_chroma_rows_when_vector_layer_disabled(AXIOM_db, monkeypatch):
    """2026-06-13 declutter: when in-process ChromaDB is disabled, the dead
    chroma + narratives source rows must be dropped from the overview (they were
    misleadingly shown as 'active / no records yet')."""
    workspace_dir = _pin_workspace(monkeypatch)
    (workspace_dir / "memory").mkdir(parents=True, exist_ok=True)
    (workspace_dir / "memory" / "MEMORY.md").write_text(
        "# Operator Notes\nWorkspace memory still loads.\n", encoding="utf-8"
    )
    monkeypatch.setenv("AXIOM_DISABLE_CHROMA_IN_PROCESS", "1")

    payload = memory_domain.get_memory_overview()
    sources = {entry["source"] for entry in payload["source_health"]}

    assert "workspace" in sources  # live source still shown
    assert "chroma" not in sources  # dead vector row dropped
    assert NARRATIVES_SOURCE not in sources


def test_memory_overview_keeps_chroma_row_when_vector_layer_enabled(AXIOM_db, monkeypatch):
    workspace_dir = _pin_workspace(monkeypatch)
    (workspace_dir / "memory").mkdir(parents=True, exist_ok=True)
    monkeypatch.delenv("AXIOM_DISABLE_CHROMA_IN_PROCESS", raising=False)
    monkeypatch.setattr(memory_domain, "_run_chroma_subprocess", lambda payload, timeout=60: {"ok": True, "collections": {}})
    monkeypatch.setattr(memory_domain, "_browse_narrative_items_sync", lambda limit: [])

    payload = memory_domain.get_memory_overview()
    sources = {entry["source"] for entry in payload["source_health"]}
    assert {"workspace", "chroma", NARRATIVES_SOURCE}.issubset(sources)


def test_memory_overview_browses_live_narratives_by_default(AXIOM_db, monkeypatch):
    workspace_dir = _pin_workspace(monkeypatch)
    (workspace_dir / "memory").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(memory_domain, "_run_chroma_subprocess", lambda payload, timeout=60: {"ok": True, "collections": {}})
    monkeypatch.setattr(
        memory_domain,
        "_narrative_query_results",
        lambda query, limit: [
            {
                "id": "narr-live-1",
                "document": "The brain already learned that staggered sizing beats all-in entries.",
                "metadata": {"agent": "brain", "type": "lesson", "recorded_at": "2026-03-12T02:00:00Z"},
            }
        ],
    )

    payload = memory_domain.get_memory_overview()

    narrative_item = next(item for item in payload["recent_items"] if item["source"] == NARRATIVES_SOURCE)
    assert narrative_item["source_id"] == "narr-live-1"

    narrative_health = next(entry for entry in payload["source_health"] if entry["source"] == NARRATIVES_SOURCE)
    assert narrative_health["count"] == 1

    detail = memory_domain.get_memory_item(NARRATIVES_SOURCE, "narr-live-1")
    assert detail["item"]["title"]
    assert "staggered sizing" in detail["item"]["content_preview"]


def test_search_memory_records_reports_live_narrative_matches(AXIOM_db, monkeypatch):
    workspace_dir = _pin_workspace(monkeypatch)
    (workspace_dir / "memory").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(memory_domain, "_run_chroma_subprocess", lambda payload, timeout=60: {"ok": True, "collections": {}})
    monkeypatch.setattr(
        memory_domain,
        "_narrative_query_results",
        lambda query, limit: [
            {
                "id": "narr-1",
                "document": "Respect drawdown clusters before increasing size.",
                "metadata": {"agent": "brain", "recorded_at": "2026-03-12T01:42:01.311Z"},
            }
        ],
    )

    payload = asyncio.run(
        memory_domain.search_memory_records(
            memory_domain.MemorySearchRequest(
                query="drawdown",
                sources=[NARRATIVES_SOURCE],
            )
        )
    )

    narrative_health = next(entry for entry in payload["source_health"] if entry["source"] == NARRATIVES_SOURCE)
    assert narrative_health["count"] == 1

    detail = memory_domain.get_memory_item(NARRATIVES_SOURCE, "narr-1")
    assert "drawdown clusters" in detail["item"]["content_preview"]


def test_apply_memory_action_hide_toggles_narrative(AXIOM_db, monkeypatch):
    detail = memory_domain.update_memory_annotation(
        NARRATIVES_SOURCE,
        "narr-hide-1",
        memory_domain.MemoryAnnotationBody(
            title_override="Remember this",
            item_snapshot={
                "source": NARRATIVES_SOURCE,
                "source_kind": "narrative",
                "source_id": "narr-hide-1",
                "title": "Remember this",
                "excerpt": "Long-term recall entry",
                "content_preview": "Long-term recall entry",
                "tags": ["lesson"],
                "actions": ["annotate", "hide", "unhide"],
            },
        ),
    )

    assert detail["item"]["hidden"] is False

    response = asyncio.run(
        memory_domain.apply_memory_action(
            NARRATIVES_SOURCE,
            "narr-hide-1",
            memory_domain.MemoryActionBody(
                action="hide",
                item_snapshot=detail["item"],
            ),
        )
    )

    assert response["ok"] is True
    assert response["action"] == "hide"
    assert response["item"]["hidden"] is True


def test_memory_maintenance_preview_reports_old_daily_logs(AXIOM_db, monkeypatch):
    workspace_dir = _pin_workspace(monkeypatch)
    daily_dir = workspace_dir / "agents" / "strategy-developer" / "memory"
    daily_dir.mkdir(parents=True, exist_ok=True)
    # Dates are relative to now: the preview compares the filename date against
    # now - older_than_days, so hardcoded calendar dates silently age out of the
    # window over time. "old" must be well past 30 days; "recent" well within.
    now = datetime.now(timezone.utc)
    old_name = f"{(now - timedelta(days=120)).strftime('%Y-%m-%d')}.md"
    recent_name = f"{(now - timedelta(days=5)).strftime('%Y-%m-%d')}.md"
    (daily_dir / old_name).write_text(
        "# Daily Log\nRoutine generated chatter.\n\n## Failure S12345\nRisk gate failed.\n",
        encoding="utf-8",
    )
    (daily_dir / recent_name).write_text(
        "# Daily Log\nRecent generated chatter.\n",
        encoding="utf-8",
    )

    payload = memory_domain.get_memory_maintenance_preview(older_than_days=30)

    assert payload["dry_run"] is True
    assert payload["summary"]["daily_log_files_to_compact"] == 1
    assert payload["summary"]["daily_file_items_to_hide"] == 1
    assert payload["summary"]["daily_signal_sections_seen"] == 1
    assert payload["agent_counts"]["strategy-developer"] == 2
    candidates = payload["candidates"]["daily_file_items"]
    assert candidates[0]["source_id"] == f"agents/strategy-developer/memory/{old_name}"


def test_memory_maintenance_preview_skips_annotated_daily_logs(AXIOM_db, monkeypatch):
    workspace_dir = _pin_workspace(monkeypatch)
    daily_dir = workspace_dir / "agents" / "brain" / "memory"
    daily_dir.mkdir(parents=True, exist_ok=True)
    (daily_dir / "2026-01-01.md").write_text(
        "# Daily Log\nOperator-important daily note.\n",
        encoding="utf-8",
    )

    memory_domain.update_memory_annotation(
        "workspace",
        "agents/brain/memory/2026-01-01.md",
        memory_domain.MemoryAnnotationBody(
            note="Keep this raw log visible.",
            tier="working",
        ),
    )

    payload = memory_domain.get_memory_maintenance_preview(older_than_days=30)

    assert payload["summary"]["daily_log_files_to_compact"] == 0
    assert payload["summary"]["daily_file_items_to_hide"] == 0
    assert payload["summary"]["protected_daily_items"] == 1


def test_memory_maintenance_run_writes_summary_and_hides_raw_daily_log(AXIOM_db, monkeypatch):
    workspace_dir = _pin_workspace(monkeypatch)
    daily_dir = workspace_dir / "agents" / "strategy-developer" / "memory"
    daily_dir.mkdir(parents=True, exist_ok=True)
    (daily_dir / "2026-01-01.md").write_text(
        "# Daily Log\nRoutine generated chatter.\n\n## Failure S12345\nRisk gate failed.\n",
        encoding="utf-8",
    )

    payload = memory_domain.run_memory_maintenance(
        memory_domain.MemoryMaintenanceRequest(
            dry_run=False,
            older_than_days=30,
            limit=10,
        )
    )

    summary_path = daily_dir / "summaries" / "2026-01-01.md"
    assert summary_path.exists()
    summary_text = summary_path.read_text(encoding="utf-8")
    assert "strategy-developer Daily Summary 2026-01-01" in summary_text
    assert "Failure S12345" in summary_text
    assert payload["applied"]["summaries_written"] == 1
    assert payload["applied"]["daily_file_items_hidden"] == 1

    detail = memory_domain.get_memory_item("workspace", "agents/strategy-developer/memory/2026-01-01.md")
    assert detail["item"]["hidden"] is True
    assert "compacted" in detail["item"]["tags"]
    assert any(event["action"] == "maintenance_compact" for event in detail["events"])

    signal_detail = memory_domain.get_memory_item(
        "workspace",
        "agents/strategy-developer/memory/2026-01-01.md#failure-s12345",
    )
    assert signal_detail["item"]["hidden"] is False


def test_memory_router_search_and_annotation_round_trip(AXIOM_db, monkeypatch):
    workspace_dir = _pin_workspace(monkeypatch)
    (workspace_dir / "memory").mkdir(parents=True, exist_ok=True)
    (workspace_dir / "memory" / "MEMORY.md").write_text(
        "# Regime Lesson\nDrawdown regime matters more than raw Sharpe.\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(memory_domain, "_run_chroma_subprocess", lambda payload, timeout=60: {"ok": True, "collections": {}})

    app = FastAPI()
    app.include_router(memory_router)
    client = TestClient(app)

    search_response = client.post(
        "/api/memory/search",
        json={"query": "drawdown regime", "sources": ["workspace"]},
    )
    assert search_response.status_code == 200
    results = search_response.json()["results"]
    assert results

    first_item = results[0]
    encoded_id = quote(first_item["source_id"], safe="")

    annotation_response = client.put(
        f"/api/memory/item/workspace/{encoded_id}/annotation",
        json={
            "title_override": "Canon Regime Lesson",
            "tags": ["canon", "regime"],
            "tier": "canon",
            "pinned": True,
            "item_snapshot": first_item,
        },
    )

    assert annotation_response.status_code == 200
    item = annotation_response.json()["item"]
    assert item["title"] == "Canon Regime Lesson"
    assert item["pinned"] is True
    assert item["tier"] == "canon"


def test_memory_router_maintenance_preview_and_dry_run(AXIOM_db, monkeypatch):
    workspace_dir = _pin_workspace(monkeypatch)
    daily_dir = workspace_dir / "agents" / "brain" / "memory"
    daily_dir.mkdir(parents=True, exist_ok=True)
    (daily_dir / "2026-01-01.md").write_text(
        "# Daily Log\nRoutine generated chatter.\n",
        encoding="utf-8",
    )

    app = FastAPI()
    app.include_router(memory_router)
    client = TestClient(app)

    preview_response = client.get("/api/memory/maintenance/preview?older_than_days=30")
    assert preview_response.status_code == 200
    assert preview_response.json()["summary"]["daily_file_items_to_hide"] == 1

    dry_run_response = client.post(
        "/api/memory/maintenance/run",
        json={"dry_run": True, "older_than_days": 30},
    )
    assert dry_run_response.status_code == 200
    assert dry_run_response.json()["summary"]["daily_file_items_to_hide"] == 1

    run_response = client.post(
        "/api/memory/maintenance/run",
        json={"dry_run": False, "older_than_days": 30},
    )
    assert run_response.status_code == 200
    assert run_response.json()["applied"]["daily_file_items_hidden"] == 1

    no_action_response = client.post(
        "/api/memory/maintenance/run",
        json={
            "dry_run": False,
            "compact_daily_logs": False,
            "hide_old_daily_logs": False,
            "archive_narratives": False,
            "older_than_days": 30,
        },
    )
    assert no_action_response.status_code == 400
