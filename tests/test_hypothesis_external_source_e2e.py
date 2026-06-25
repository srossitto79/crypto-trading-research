from __future__ import annotations

from axiom.hypotheses import (
    add_hypothesis_artifact,
    create_hypothesis,
    list_hypothesis_artifacts,
)


def _make_hypothesis():
    return create_hypothesis(
        title="Cross-source evidence hypothesis",
        market_thesis="Multi-source evidence strengthens the hypothesis signal.",
        mechanism="Aggregate signals across independent sources.",
        why_now="E2E smoke test.",
        lane="benchmarking",
        source_type="public_benchmark",
        origin_agent_id="agent-smoke",
        origin_role="strategy-developer",
        target_assets=["BTC-PERP"],
        target_timeframes=["1h"],
    )


def test_all_five_source_types_attach_with_cached_content(AXIOM_db):
    hyp = _make_hypothesis()

    payloads = {
        "youtube": "transcript body A",
        "reddit":  "reddit thread body B",
        "blog":    "blog article body C",
        "github":  "github readme body D",
        "forum":   "forum thread body E",
    }

    for source_type, content in payloads.items():
        art = add_hypothesis_artifact(
            hypothesis_id=hyp["id"],
            source_type=source_type,
            source_title=f"{source_type} exemplar",
            source_ref=f"https://example.com/{source_type}",
            claimed_edge="illustrative edge",
            implementation_summary="illustrative method",
            cached_content=content,
        )
        assert art["source_type"] == source_type
        assert art["cached_content"] == content
        assert art["cached_content_hash"] is not None
        assert art["content_bytes"] == len(content.encode("utf-8"))

    artifacts = list_hypothesis_artifacts(hyp["id"])
    types = {a["source_type"] for a in artifacts}
    assert types == set(payloads.keys())

    hashes = {a["cached_content_hash"] for a in artifacts}
    assert len(hashes) == 5  # distinct content per source

    for a in artifacts:
        assert a["cached_content"]
        assert a["content_bytes"] > 0


def test_duplicate_content_across_sources_produces_same_hash(AXIOM_db):
    """Shared hash across sources lets agents dedupe identical evidence even when it appears in multiple places."""
    hyp = _make_hypothesis()

    identical = "the same evidence body"
    art_a = add_hypothesis_artifact(
        hypothesis_id=hyp["id"], source_type="reddit",
        source_title="r", source_ref="https://example.com/reddit",
        claimed_edge="e", implementation_summary="s",
        cached_content=identical,
    )
    art_b = add_hypothesis_artifact(
        hypothesis_id=hyp["id"], source_type="blog",
        source_title="b", source_ref="https://example.com/blog",
        claimed_edge="e", implementation_summary="s",
        cached_content=identical,
    )
    assert art_a["cached_content_hash"] == art_b["cached_content_hash"]
    assert art_a["id"] != art_b["id"]  # still distinct rows


def test_empty_cached_content_permitted(AXIOM_db):
    """inspect_*() may return empty content (unextractable HTML, deleted post, etc.).
    The artifact still attaches; cached_content is empty string; hash is sha256('').
    """
    import hashlib
    hyp = _make_hypothesis()
    art = add_hypothesis_artifact(
        hypothesis_id=hyp["id"], source_type="blog",
        source_title="t", source_ref="https://example.com/blog",
        claimed_edge="e", implementation_summary="s",
        cached_content="",
    )
    assert art["cached_content"] == ""
    assert art["cached_content_hash"] == hashlib.sha256(b"").hexdigest()
    assert art["content_bytes"] == 0


def test_detail_api_exposes_all_source_types_with_hash_not_content(AXIOM_db):
    """Detail API default response strips cached_content but keeps hash/bytes/cached_at."""
    from fastapi.testclient import TestClient
    from axiom.api import app

    hyp = _make_hypothesis()
    for st, content in (("youtube", "A"), ("reddit", "B"), ("blog", "C"), ("github", "D"), ("forum", "E")):
        add_hypothesis_artifact(
            hypothesis_id=hyp["id"], source_type=st,
            source_title=st, source_ref=f"https://example.com/{st}",
            claimed_edge="e", implementation_summary="s",
            cached_content=content,
        )

    client = TestClient(app)
    # Default: content stripped
    r = client.get(f"/api/hypotheses/{hyp['id']}")
    assert r.status_code == 200
    body = r.json()
    artifacts = body["artifacts"]
    assert {a["source_type"] for a in artifacts} == {"youtube", "reddit", "blog", "github", "forum"}
    for a in artifacts:
        assert a["cached_content"] is None
        assert a["cached_content_hash"] is not None
        assert a["content_bytes"] is not None

    # Opt-in: content returned
    r = client.get(f"/api/hypotheses/{hyp['id']}?include=content")
    assert r.status_code == 200
    for a in r.json()["artifacts"]:
        assert a["cached_content"]  # truthy, not None
