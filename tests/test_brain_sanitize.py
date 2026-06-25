"""Phase 1 (P1-T05) — fence-echo sanitizer tests.

Covers the regex strip behavior, idempotency, persistent counter, log
warning emission, and the diagnostics check that reports the count.
"""
from __future__ import annotations

import logging

from axiom.sanitize import (
    fence_strip_count,
    sanitize_operator_input,
    strip_brain_context_fences,
)
from axiom.diagnostics import check_brain_fence_strips


def test_strip_returns_input_unchanged_when_no_fence(AXIOM_db):
    text = "no fence here, just plain operator chat"
    cleaned, count = strip_brain_context_fences(text)
    assert cleaned == text
    assert count == 0


def test_strip_removes_simple_fence_block(AXIOM_db):
    text = "before <brain-context>\nbody\n</brain-context> after"
    cleaned, count = strip_brain_context_fences(text)
    assert cleaned == "before  after"
    assert count == 1


def test_strip_removes_multiple_fence_blocks(AXIOM_db):
    text = "<brain-context>a</brain-context> mid <brain-context>b</brain-context>"
    cleaned, count = strip_brain_context_fences(text)
    assert cleaned == " mid "
    assert count == 2


def test_strip_handles_attributes_on_open_tag(AXIOM_db):
    text = '<brain-context source="ops" version="2">payload</brain-context>'
    cleaned, count = strip_brain_context_fences(text)
    assert cleaned == ""
    assert count == 1


def test_strip_handles_multiline_block(AXIOM_db):
    text = "intro\n<brain-context>\nline1\nline2\nline3\n</brain-context>\noutro"
    cleaned, count = strip_brain_context_fences(text)
    assert cleaned == "intro\n\noutro"
    assert count == 1


def test_strip_is_idempotent(AXIOM_db):
    text = "x<brain-context>y</brain-context>z"
    once, _ = strip_brain_context_fences(text)
    twice, count_two = strip_brain_context_fences(once)
    assert twice == once
    assert count_two == 0


def test_strip_handles_empty_input(AXIOM_db):
    assert strip_brain_context_fences("") == ("", 0)
    assert strip_brain_context_fences(None) == ("", 0)


def test_sanitize_increments_counter_and_returns_clean_text(AXIOM_db):
    before = fence_strip_count()
    out = sanitize_operator_input(
        "hello <brain-context>injected</brain-context> world",
        source="test",
    )
    assert "<brain-context>" not in out
    assert "hello" in out and "world" in out
    after = fence_strip_count()
    assert after == before + 1


def test_sanitize_no_fence_does_not_bump_counter(AXIOM_db):
    before = fence_strip_count()
    out = sanitize_operator_input("plain chat message", source="test")
    assert out == "plain chat message"
    assert fence_strip_count() == before


def test_sanitize_logs_warning(AXIOM_db, caplog):
    caplog.set_level(logging.WARNING, logger="axiom.sanitize")
    sanitize_operator_input(
        "<brain-context>x</brain-context>", source="unit-test-source"
    )
    assert any(
        "stripped" in rec.message and "unit-test-source" in rec.message
        for rec in caplog.records
    )


def test_diagnostics_check_reports_strip_count(AXIOM_db):
    sanitize_operator_input("<brain-context>a</brain-context>", source="t")
    sanitize_operator_input("<brain-context>b</brain-context>", source="t")
    res = check_brain_fence_strips()
    assert res.name == "brain_fence_strips"
    assert res.status == "pass"
    assert res.detail["count"] >= 2


def test_case_insensitive_open_tag(AXIOM_db):
    text = "<Brain-Context>x</brain-context>"
    cleaned, count = strip_brain_context_fences(text)
    assert cleaned == ""
    assert count == 1
