from __future__ import annotations

from fastapi.testclient import TestClient

from axiom.db import get_db


def test_list_hypotheses_returns_operator_fields(AXIOM_db):
    from axiom.api import app
    from axiom.hypotheses import create_hypothesis, record_data_gap

    hypothesis = create_hypothesis(
        title="Funding dislocation mean reversion",
        market_thesis="Crowded positive funding precedes short-term mean reversion.",
        mechanism="Fade stretched funding after liquidation spikes.",
        lane="exploration",
        source_type="agent_original",
        origin_agent_id="strategy-developer",
        origin_model="gpt-5.4",
        target_assets=["BTC-PERP"],
        target_timeframes=["15m"],
        novelty_score=0.73,
    )
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO strategies (id, name, hypothesis_id, stage, status, symbol, timeframe, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
            """,
            ("S-HYP-API", "Funding fade", hypothesis["id"], "quick_screen", "quick_screen", "BTC-PERP", "15m"),
        )
    record_data_gap(
        title="Funding history",
        category="derivatives",
        missing_dataset="funding_rates",
        linked_hypothesis_id=hypothesis["id"],
    )

    client = TestClient(app)
    response = client.get("/api/hypotheses")

    assert response.status_code == 200
    body = response.json()
    assert "hypotheses" in body
    assert len(body["hypotheses"]) == 1
    item = body["hypotheses"][0]
    assert item["id"] == hypothesis["id"]
    assert item["display_id"] == "H00001"
    assert item["lane"] == "exploration"
    assert item["origin_agent_id"] == "strategy-developer"
    assert item["manager_state"] == "active"
    assert item["archived_at"] is None
    assert item["deleted_at"] is None
    assert item["restored_at"] is None
    assert item["strategy_count"] == 1
    assert item["open_data_gap_count"] == 1


def test_get_hypothesis_detail_returns_strategies_artifacts_and_data_gaps(AXIOM_db):
    from axiom.api import app
    from axiom.hypotheses import add_hypothesis_artifact, create_hypothesis, record_data_gap

    hypothesis = create_hypothesis(
        title="Public benchmark idea",
        market_thesis="A public funding strategy can be adapted to liquid perps.",
        mechanism="Normalize the public playbook into a hypothesis-first record.",
        lane="benchmarking",
        source_type="public_benchmark",
        target_assets=["ETH-PERP"],
        target_timeframes=["1h"],
    )
    add_hypothesis_artifact(
        hypothesis_id=hypothesis["id"],
        source_type="youtube",
        source_title="Funding walkthrough",
        source_ref="https://example.com/funding-video",
        claimed_edge="Funding extremes mean revert",
        implementation_summary="Fade crowded funding after liquidation spikes",
    )
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO strategies (id, name, hypothesis_id, stage, status, symbol, timeframe, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
            """,
            ("S-HYP-DETAIL", "Funding adaptation", hypothesis["id"], "research_only", "research_only", "ETH-PERP", "1h"),
        )
    record_data_gap(
        title="Liquidation feed",
        category="derivatives",
        missing_dataset="liquidations",
        linked_strategy_id="S-HYP-DETAIL",
    )

    client = TestClient(app)
    response = client.get("/api/hypotheses/H00001")

    assert response.status_code == 200
    body = response.json()
    assert body["hypothesis"]["id"] == hypothesis["id"]
    assert body["hypothesis"]["display_id"] == "H00001"
    assert body["hypothesis"]["manager_state"] == "active"
    assert body["artifacts"][0]["source_type"] == "youtube"
    assert body["strategies"][0]["id"] == "S-HYP-DETAIL"
    assert body["data_gaps"][0]["missing_dataset"] == "liquidations"


def test_list_hypotheses_accepts_manager_view_and_search(AXIOM_db):
    from axiom.api import app
    from axiom.hypotheses import archive_hypothesis, create_hypothesis

    archived = create_hypothesis(
        title="Funding search target",
        market_thesis="Archived funding hypotheses should be filterable.",
        mechanism="Archive then query by manager view and search.",
        lane="exploration",
        source_type="agent_original",
        target_assets=["BTC-PERP"],
        target_timeframes=["1h"],
    )
    create_hypothesis(
        title="Unrelated active idea",
        market_thesis="This one should stay active.",
        mechanism="Leave it active so it does not show up in archived view.",
        lane="benchmarking",
        source_type="public_benchmark",
        target_assets=["ETH-PERP"],
        target_timeframes=["4h"],
    )
    archive_hypothesis(archived["id"])

    client = TestClient(app)
    response = client.get("/api/hypotheses?view=archived&search=funding")

    assert response.status_code == 200
    body = response.json()
    assert [item["id"] for item in body["hypotheses"]] == [archived["id"]]
    assert body["hypotheses"][0]["manager_state"] == "archived"


def test_hypothesis_lifecycle_endpoints_transition_and_return_payload(AXIOM_db):
    from axiom.api import app
    from axiom.hypotheses import create_hypothesis

    created = create_hypothesis(
        title="API lifecycle coverage",
        market_thesis="Expose archive trash restore through the router.",
        mechanism="Transition one hypothesis through each operator state.",
        lane="exploration",
        source_type="agent_original",
        target_assets=["BTC-PERP"],
        target_timeframes=["15m"],
    )

    client = TestClient(app)
    archived = client.post(f"/api/hypotheses/{created['id']}/archive")
    trashed = client.post(f"/api/hypotheses/{created['display_id']}/trash")
    restored = client.post(f"/api/hypotheses/{created['id']}/restore")

    assert archived.status_code == 200
    assert archived.json()["hypothesis"]["manager_state"] == "archived"
    assert trashed.status_code == 200
    assert trashed.json()["hypothesis"]["manager_state"] == "trash"
    assert restored.status_code == 200
    assert restored.json()["hypothesis"]["manager_state"] == "active"
    assert restored.json()["hypothesis"]["restored_at"] is not None


def test_bulk_hypothesis_lifecycle_endpoints_accept_selected_ids(AXIOM_db):
    from axiom.api import app
    from axiom.hypotheses import create_hypothesis

    first = create_hypothesis(
        title="First API bulk hypothesis",
        market_thesis="Operators need bulk archive.",
        mechanism="Archive several rows at once.",
        lane="exploration",
        source_type="agent_original",
        target_assets=["BTC-PERP"],
        target_timeframes=["1h"],
    )
    second = create_hypothesis(
        title="Second API bulk hypothesis",
        market_thesis="Operators need bulk archive.",
        mechanism="Archive several rows at once.",
        lane="benchmarking",
        source_type="public_benchmark",
        target_assets=["ETH-PERP"],
        target_timeframes=["4h"],
    )

    client = TestClient(app)
    response = client.post(
        "/api/hypotheses/bulk/archive",
        json={"ids": [first["id"], second["display_id"], "HYP-UNKNOWN"]},
    )

    assert response.status_code == 200
    body = response.json()
    assert {item["id"] for item in body["hypotheses"]} == {first["id"], second["id"]}
    assert {item["manager_state"] for item in body["hypotheses"]} == {"archived"}


def test_ranked_data_gaps_endpoint_returns_top_blockers(AXIOM_db):
    from axiom.api import app
    from axiom.hypotheses import create_hypothesis, record_data_gap

    hypothesis = create_hypothesis(
        title="Gap leaderboard",
        market_thesis="Operators need the top blockers surfaced.",
        mechanism="Aggregate repeated requests into a ranked list.",
        lane="exploration",
        source_type="agent_original",
        target_assets=["BTC-PERP"],
        target_timeframes=["1h"],
    )
    record_data_gap(
        title="Funding history",
        category="derivatives",
        missing_dataset="funding_rates",
        linked_hypothesis_id=hypothesis["id"],
        priority_score=0.9,
    )

    client = TestClient(app)
    response = client.get("/api/data-gaps?limit=5")

    assert response.status_code == 200
    body = response.json()
    assert "items" in body
    assert body["items"][0]["missing_dataset"] == "funding_rates"


def test_list_hypotheses_pagination_is_backward_compatible_and_slices(AXIOM_db):
    from axiom.api import app
    from axiom.hypotheses import create_hypothesis

    for i in range(5):
        create_hypothesis(
            title=f"Paging idea {i}",
            market_thesis="Operators need server-side paging.",
            mechanism="Return a page slice plus a total count.",
            lane="exploration",
            source_type="agent_original",
            target_assets=["BTC-PERP"],
            target_timeframes=["1h"],
        )

    client = TestClient(app)

    # No limit/offset -> legacy shape (just hypotheses), full list.
    legacy = client.get("/api/hypotheses").json()
    assert "total" not in legacy
    assert len(legacy["hypotheses"]) == 5

    # First page of 2.
    page1 = client.get("/api/hypotheses?limit=2&offset=0").json()
    assert page1["total"] == 5
    assert page1["limit"] == 2
    assert page1["offset"] == 0
    assert len(page1["hypotheses"]) == 2

    # Second page of 2 does not overlap the first.
    page2 = client.get("/api/hypotheses?limit=2&offset=2").json()
    assert page2["total"] == 5
    assert len(page2["hypotheses"]) == 2
    page1_ids = {h["id"] for h in page1["hypotheses"]}
    page2_ids = {h["id"] for h in page2["hypotheses"]}
    assert page1_ids.isdisjoint(page2_ids)

    # Offset past the end yields an empty page but the same total.
    tail = client.get("/api/hypotheses?limit=2&offset=10").json()
    assert tail["total"] == 5
    assert tail["hypotheses"] == []


def test_hypotheses_counts_endpoint_returns_all_buckets(AXIOM_db):
    from axiom.api import app
    from axiom.hypotheses import archive_hypothesis, create_hypothesis, trash_hypothesis

    client = TestClient(app)
    # Baseline: init_db may seed a legacy archived hypothesis, so compare deltas.
    baseline = client.get("/api/hypotheses/counts").json()["counts"]

    active = create_hypothesis(
        title="Active bucket",
        market_thesis="Counts cover active.",
        mechanism="Stay active.",
        lane="exploration",
        source_type="agent_original",
        target_assets=["BTC-PERP"],
        target_timeframes=["1h"],
    )
    to_archive = create_hypothesis(
        title="Archived bucket",
        market_thesis="Counts cover archived.",
        mechanism="Archive me.",
        lane="exploration",
        source_type="agent_original",
        target_assets=["ETH-PERP"],
        target_timeframes=["4h"],
    )
    to_trash = create_hypothesis(
        title="Trashed bucket",
        market_thesis="Counts cover trash.",
        mechanism="Trash me.",
        lane="benchmarking",
        source_type="public_benchmark",
        target_assets=["SOL-PERP"],
        target_timeframes=["15m"],
    )
    archive_hypothesis(to_archive["id"])
    trash_hypothesis(to_trash["id"])

    response = client.get("/api/hypotheses/counts")

    assert response.status_code == 200
    counts = response.json()["counts"]
    assert counts["active"] - baseline.get("active", 0) == 1
    assert counts["archived"] - baseline.get("archived", 0) == 1
    assert counts["trash"] - baseline.get("trash", 0) == 1
    assert counts["graduated"] - baseline.get("graduated", 0) == 0
    # Sanity: counts agree with paginated totals for the same buckets.
    assert client.get("/api/hypotheses?view=active&limit=1").json()["total"] == counts["active"]
    assert active["id"]  # referenced so the active row is meaningful


def test_ranked_data_gaps_surface_requesting_hypotheses(AXIOM_db):
    from axiom.api import app
    from axiom.hypotheses import create_hypothesis, record_data_gap

    direct = create_hypothesis(
        title="Direct requester",
        market_thesis="A gap can be requested directly by a hypothesis.",
        mechanism="Link the gap straight to the hypothesis.",
        lane="exploration",
        source_type="agent_original",
        target_assets=["BTC-PERP"],
        target_timeframes=["1h"],
    )
    via_strategy = create_hypothesis(
        title="Strategy requester",
        market_thesis="A gap can be requested via one of the hypothesis strategies.",
        mechanism="Link the gap to a strategy owned by the hypothesis.",
        lane="benchmarking",
        source_type="public_benchmark",
        target_assets=["ETH-PERP"],
        target_timeframes=["4h"],
    )
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO strategies (id, name, hypothesis_id, stage, status, symbol, timeframe, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
            """,
            ("S-GAP-REQ", "Gap strat", via_strategy["id"], "quick_screen", "quick_screen", "ETH-PERP", "4h"),
        )

    # One gap requested by both: directly by `direct`, and via `via_strategy`'s strategy.
    record_data_gap(
        title="Shared funding feed",
        category="derivatives",
        missing_dataset="funding_rates",
        linked_hypothesis_id=direct["id"],
        priority_score=0.9,
    )
    record_data_gap(
        title="Shared funding feed",
        category="derivatives",
        missing_dataset="funding_rates",
        linked_strategy_id="S-GAP-REQ",
        priority_score=0.9,
    )

    client = TestClient(app)
    response = client.get("/api/data-gaps?limit=5")

    assert response.status_code == 200
    items = response.json()["items"]
    assert items, "expected at least one ranked gap"
    top = items[0]
    assert top["missing_dataset"] == "funding_rates"
    requester_ids = set(top.get("requesting_hypothesis_ids") or [])
    assert requester_ids == {direct["id"], via_strategy["id"]}
    requesters = {r["id"]: r for r in (top.get("requesting_hypotheses") or [])}
    assert requesters[direct["id"]]["display_id"]
    assert requesters[via_strategy["id"]]["title"] == "Strategy requester"


def test_get_hypothesis_detail_returns_404_for_unknown_id(AXIOM_db):
    from axiom.api import app

    client = TestClient(app)
    response = client.get("/api/hypotheses/HYP-UNKNOWN")

    assert response.status_code == 404


def test_strategy_container_includes_parent_hypothesis_id(AXIOM_db):
    from axiom.api import app
    from axiom.hypotheses import create_hypothesis

    hypothesis = create_hypothesis(
        title="Container backlink",
        market_thesis="Strategies should keep their hypothesis link in detail views.",
        mechanism="Expose hypothesis_id through the strategy container payload.",
        lane="exploration",
        source_type="agent_original",
        target_assets=["BTC-PERP"],
        target_timeframes=["1h"],
    )
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO strategies (
                id, name, hypothesis_id, stage, status, symbol, timeframe, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
            """,
            ("S-HYP-CONTAINER", "Backlinked strategy", hypothesis["id"], "quick_screen", "quick_screen", "BTC-PERP", "1h"),
        )

    client = TestClient(app)
    response = client.get("/api/strategies/S-HYP-CONTAINER/container")

    assert response.status_code == 200
    body = response.json()
    assert body["strategy"]["id"] == "S-HYP-CONTAINER"
    assert body["strategy"]["hypothesis_id"] == hypothesis["id"]


def test_detail_excludes_cached_content_by_default(AXIOM_db):
    from axiom.api import app
    from axiom.hypotheses import add_hypothesis_artifact, create_hypothesis

    hyp = create_hypothesis(
        title="t",
        market_thesis="m",
        mechanism="x",
        why_now="n",
        lane="benchmarking",
        source_type="public_benchmark",
        origin_agent_id="a",
        origin_role="strategy-developer",
        target_assets=["BTC-PERP"],
        target_timeframes=["1h"],
    )
    add_hypothesis_artifact(
        hypothesis_id=hyp["id"],
        source_type="reddit",
        source_title="T",
        source_ref="https://x/y",
        claimed_edge="e",
        implementation_summary="s",
        cached_content="the cached body",
    )

    client = TestClient(app)
    response = client.get(f"/api/hypotheses/{hyp['id']}")

    assert response.status_code == 200
    body = response.json()
    assert len(body["artifacts"]) == 1
    art = body["artifacts"][0]
    assert art["cached_content"] is None
    # Hash/size still exposed so agents/UI can see something is cached
    assert art["cached_content_hash"] is not None
    assert art["content_bytes"] is not None


def test_detail_includes_cached_content_when_requested(AXIOM_db):
    from axiom.api import app
    from axiom.hypotheses import add_hypothesis_artifact, create_hypothesis

    hyp = create_hypothesis(
        title="t",
        market_thesis="m",
        mechanism="x",
        why_now="n",
        lane="benchmarking",
        source_type="public_benchmark",
        origin_agent_id="a",
        origin_role="strategy-developer",
        target_assets=["BTC-PERP"],
        target_timeframes=["1h"],
    )
    add_hypothesis_artifact(
        hypothesis_id=hyp["id"],
        source_type="reddit",
        source_title="T",
        source_ref="https://x/y",
        claimed_edge="e",
        implementation_summary="s",
        cached_content="the cached body",
    )

    client = TestClient(app)
    response = client.get(f"/api/hypotheses/{hyp['id']}?include=content")

    assert response.status_code == 200
    body = response.json()
    assert body["artifacts"][0]["cached_content"] == "the cached body"


def test_discover_endpoint_triggers_discovery_on_operator_demand(AXIOM_db):
    """POST /api/hypotheses/discover runs discovery even though the autonomous_discovery
    setting is OFF by default (operator demand), and dedups through the route."""
    from axiom.api import app

    client = TestClient(app)
    first = client.post("/api/hypotheses/discover")
    assert first.status_code == 200
    body = first.json()
    assert body["created"] is True
    assert body.get("mode")

    second = client.post("/api/hypotheses/discover")
    assert second.status_code == 200
    assert second.json()["created"] is False
    assert second.json()["reason"] == "already_open"
