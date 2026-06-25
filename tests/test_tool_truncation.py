"""Tests for tool output truncation + redaction in tool_registry."""

import gzip


from axiom.agents.tool_registry import (
    DEFAULT_MAX_BYTES,
    DEFAULT_MAX_CHARS_PER_LINE,
    DEFAULT_MAX_LINES,
    TRUNCATION_LINE_MARKER,
    _process_tool_output,
)
from axiom.db import get_db


def _read_truncation_row(row_id: int) -> dict:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM tool_truncations WHERE id = ?", (row_id,)
        ).fetchone()
    assert row is not None
    return dict(row)


def test_short_output_passes_through():
    out = _process_tool_output(
        "hello world",
        tool_name="test_tool",
        task_display_id=None,
        agent_id=None,
    )
    assert out == "hello world"


def test_byte_cap_fires():
    big = "x" * (DEFAULT_MAX_BYTES + 1000)
    out = _process_tool_output(
        big,
        tool_name="test_tool",
        task_display_id="T99001",
        agent_id="agent-test",
    )
    # Truncated body + footer
    assert "[output truncated:" in out
    assert "bytes" in out
    # Visible portion shouldn't be longer than ~ MAX_BYTES + footer
    assert len(out.encode("utf-8")) < DEFAULT_MAX_BYTES + 500


def test_line_cap_fires():
    text = "\n".join(f"line-{i}" for i in range(DEFAULT_MAX_LINES + 50))
    out = _process_tool_output(
        text,
        tool_name="test_tool",
        task_display_id="T99002",
        agent_id="agent-test",
    )
    assert "[output truncated:" in out
    assert "lines" in out


def test_chars_per_line_cap_fires():
    big_line = "y" * (DEFAULT_MAX_CHARS_PER_LINE + 500)
    out = _process_tool_output(
        big_line,
        tool_name="test_tool",
        task_display_id="T99003",
        agent_id="agent-test",
    )
    assert TRUNCATION_LINE_MARKER in out
    assert "chars_per_line" in out


def test_redaction_runs_before_truncation(AXIOM_db):
    """A secret placed at byte 60_000 (past the cap) should still be redacted
    in the persisted full output — proving redact runs before truncation."""
    pad = "x" * 60_000
    secret = "sk-proj-abcdefghij1234567890ABCDEFGHIJ"
    text = pad + " " + secret + " " + ("z" * 10_000)

    out = _process_tool_output(
        text,
        tool_name="test_redact_then_trunc",
        task_display_id="T99004",
        agent_id="agent-test",
    )
    # Visible truncated portion should not contain the secret.
    assert secret not in out
    # Find the truncation row and verify the persisted full output is also redacted.
    with get_db() as conn:
        row = conn.execute(
            "SELECT id, full_output FROM tool_truncations "
            "WHERE task_display_id = 'T99004' AND tool_name = 'test_redact_then_trunc' "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert row is not None
    full = gzip.decompress(row["full_output"]).decode("utf-8")
    # Secret should NOT appear in the persisted full output either.
    assert secret not in full
    assert "***REDACTED***" in full


def test_truncation_row_metadata(AXIOM_db):
    # Many short lines so we trip the byte cap (not the per-line cap).
    text = "\n".join("z" * 100 for _ in range(600))  # ~60 KB, lines under 2000 chars
    out = _process_tool_output(
        text,
        tool_name="metadata_test",
        task_display_id="T99005",
        agent_id="agent-meta",
    )
    assert "[output truncated:" in out
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM tool_truncations "
            "WHERE task_display_id = 'T99005' AND tool_name = 'metadata_test' "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert row is not None
    assert row["agent_id"] == "agent-meta"
    assert row["original_bytes"] > DEFAULT_MAX_BYTES
    assert row["truncated_bytes"] < row["original_bytes"]
    assert "bytes" in (row["cap_fired"] or "")


def test_unicode_safe_byte_truncation():
    """Cutting at byte boundary in a multi-byte char should not raise."""
    text = "\u4e2d" * (DEFAULT_MAX_BYTES // 2)  # 3 bytes per char in UTF-8
    out = _process_tool_output(
        text,
        tool_name="unicode_test",
        task_display_id="T99006",
        agent_id="agent-test",
    )
    # Must round-trip cleanly to UTF-8
    out.encode("utf-8")


def test_non_string_coerced():
    out = _process_tool_output(
        12345,
        tool_name="coerce_test",
        task_display_id=None,
        agent_id=None,
    )
    assert out == "12345"
