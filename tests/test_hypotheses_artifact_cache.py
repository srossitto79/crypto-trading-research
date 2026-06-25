from __future__ import annotations

import hashlib

from axiom.db import get_db
from axiom.hypotheses import add_hypothesis_artifact, create_hypothesis


def _make_hypothesis():
    return create_hypothesis(
        title="t", market_thesis="m", mechanism="x",
        why_now="n", lane="benchmarking", source_type="public_benchmark",
        origin_agent_id="a", origin_role="strategy-developer",
        target_assets=["BTC-PERP"], target_timeframes=["1h"],
    )


def test_artifact_cached_columns_exist(AXIOM_db):
    with get_db() as conn:
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(hypothesis_artifacts)")}
    assert {"cached_content", "cached_content_hash", "cached_at", "content_bytes"} <= cols


def test_add_artifact_without_content_leaves_cache_columns_null(AXIOM_db):
    hyp = _make_hypothesis()
    art = add_hypothesis_artifact(
        hypothesis_id=hyp["id"], source_type="youtube", source_title="T",
        source_ref="https://x/y", claimed_edge="e", implementation_summary="s",
    )
    with get_db() as conn:
        row = conn.execute(
            "SELECT cached_content, cached_content_hash, cached_at, content_bytes FROM hypothesis_artifacts WHERE id=?",
            (art["id"],),
        ).fetchone()
    assert row["cached_content"] is None
    assert row["cached_content_hash"] is None
    assert row["cached_at"] is None
    assert row["content_bytes"] is None


def test_add_artifact_persists_cached_content(AXIOM_db):
    hyp = _make_hypothesis()
    content = "long article body " * 50
    art = add_hypothesis_artifact(
        hypothesis_id=hyp["id"], source_type="blog", source_title="T",
        source_ref="https://x/y", claimed_edge="e", implementation_summary="s",
        cached_content=content,
    )
    with get_db() as conn:
        row = conn.execute(
            "SELECT cached_content, cached_content_hash, content_bytes, cached_at FROM hypothesis_artifacts WHERE id=?",
            (art["id"],),
        ).fetchone()
    assert row["cached_content"] == content
    assert row["cached_content_hash"] == hashlib.sha256(content.encode("utf-8")).hexdigest()
    assert row["content_bytes"] == len(content.encode("utf-8"))
    assert row["cached_at"] is not None


def test_cached_content_truncated_above_cap(AXIOM_db):
    hyp = _make_hypothesis()
    big = "x" * (600 * 1024)
    art = add_hypothesis_artifact(
        hypothesis_id=hyp["id"], source_type="blog", source_title="T",
        source_ref="https://x/y", claimed_edge="e", implementation_summary="s",
        cached_content=big,
    )
    with get_db() as conn:
        row = conn.execute(
            "SELECT cached_content, content_bytes FROM hypothesis_artifacts WHERE id=?",
            (art["id"],),
        ).fetchone()
    assert "[truncated]" in row["cached_content"]
    # Cap is 500 KB; truncation marker adds a few bytes, so allow a small overage.
    assert row["content_bytes"] <= (500 * 1024) + 50


def test_truncated_hash_matches_truncated_content(AXIOM_db):
    hyp = _make_hypothesis()
    big = "x" * (600 * 1024)
    art = add_hypothesis_artifact(
        hypothesis_id=hyp["id"], source_type="blog", source_title="T",
        source_ref="https://x/y", claimed_edge="e", implementation_summary="s",
        cached_content=big,
    )
    with get_db() as conn:
        row = conn.execute(
            "SELECT cached_content, cached_content_hash FROM hypothesis_artifacts WHERE id=?",
            (art["id"],),
        ).fetchone()
    assert row["cached_content_hash"] == hashlib.sha256(row["cached_content"].encode("utf-8")).hexdigest()
