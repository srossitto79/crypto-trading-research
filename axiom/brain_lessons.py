"""Brain lessons CRUD — Brain's self-judgment knowledge base.

Stores `brain_lessons` rows: situation_pattern + lesson_text + evidence
decisions + confidence + last_validated_at. FTS5 search over situation +
lesson text uses the `brain_lessons_fts` virtual table built in P3-T01.

Brain-only: only Brain creates/updates lessons. Operators view/edit via the
/brain Lessons tab; quant agents do not see this surface.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from axiom.db import get_db

log = logging.getLogger("axiom.brain_lessons")


def _validate_inputs(
    *,
    situation_pattern: str | None = None,
    lesson_text: str | None = None,
    evidence_decisions: list[int] | None = None,
    confidence: float | None = None,
) -> None:
    """Common validation. Raises ValueError on bad input."""
    if situation_pattern is not None and not situation_pattern.strip():
        raise ValueError("situation_pattern must be non-empty")
    if lesson_text is not None and not lesson_text.strip():
        raise ValueError("lesson_text must be non-empty")
    if evidence_decisions is not None:
        if not isinstance(evidence_decisions, list) or not all(isinstance(x, int) for x in evidence_decisions):
            raise ValueError("evidence_decisions must be a list of ints")
    if confidence is not None:
        if not 0.0 <= confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")


def create_lesson(
    situation_pattern: str,
    lesson_text: str,
    evidence_decisions: list[int],
    confidence: float = 0.5,
    *,
    created_by: str = "brain",
) -> dict:
    """Insert a new brain lesson. Returns the inserted row as a dict."""
    _validate_inputs(
        situation_pattern=situation_pattern,
        lesson_text=lesson_text,
        evidence_decisions=evidence_decisions,
        confidence=confidence,
    )
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO brain_lessons "
            "(situation_pattern, lesson_text, evidence_decisions_json, confidence, created_by) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                situation_pattern.strip(),
                lesson_text.strip(),
                json.dumps(evidence_decisions),
                float(confidence),
                created_by,
            ),
        )
        lesson_id = int(cur.lastrowid)
    return get_lesson(lesson_id)  # type: ignore[return-value]


def get_lesson(lesson_id: int) -> dict | None:
    with get_db() as conn:
        row = conn.execute(
            "SELECT id, situation_pattern, lesson_text, evidence_decisions_json, "
            "confidence, created_at, last_validated_at, created_by "
            "FROM brain_lessons WHERE id = ?",
            (int(lesson_id),),
        ).fetchone()
    return _hydrate(row)


def list_lessons(*, limit: int = 50, min_confidence: float = 0.0) -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, situation_pattern, lesson_text, evidence_decisions_json, "
            "confidence, created_at, last_validated_at, created_by "
            "FROM brain_lessons "
            "WHERE confidence >= ? "
            "ORDER BY created_at DESC LIMIT ?",
            (float(min_confidence), int(limit)),
        ).fetchall()
    return [_hydrate(r) for r in rows]  # type: ignore[misc]


def update_lesson(
    lesson_id: int,
    *,
    lesson_text: str | None = None,
    situation_pattern: str | None = None,
    confidence: float | None = None,
    last_validated_at: str | None = None,
) -> dict | None:
    """Partial update. Only non-None fields are touched."""
    if all(v is None for v in (lesson_text, situation_pattern, confidence, last_validated_at)):
        return get_lesson(lesson_id)

    _validate_inputs(
        situation_pattern=situation_pattern,
        lesson_text=lesson_text,
        confidence=confidence,
    )

    sets: list[str] = []
    args: list = []
    if lesson_text is not None:
        sets.append("lesson_text = ?")
        args.append(lesson_text.strip())
    if situation_pattern is not None:
        sets.append("situation_pattern = ?")
        args.append(situation_pattern.strip())
    if confidence is not None:
        sets.append("confidence = ?")
        args.append(float(confidence))
    if last_validated_at is not None:
        sets.append("last_validated_at = ?")
        args.append(last_validated_at)

    args.append(int(lesson_id))
    with get_db() as conn:
        cur = conn.execute(
            f"UPDATE brain_lessons SET {', '.join(sets)} WHERE id = ?",
            tuple(args),
        )
        if cur.rowcount == 0:
            return None
    return get_lesson(lesson_id)


def delete_lesson(lesson_id: int) -> bool:
    with get_db() as conn:
        cur = conn.execute("DELETE FROM brain_lessons WHERE id = ?", (int(lesson_id),))
        return cur.rowcount > 0


def search_lessons(query: str, limit: int = 20) -> list[dict]:
    """FTS5 search over situation_pattern + lesson_text."""
    if not query or not query.strip():
        return []
    with get_db() as conn:
        rows = conn.execute(
            "SELECT bl.id, bl.situation_pattern, bl.lesson_text, "
            "bl.evidence_decisions_json, bl.confidence, bl.created_at, "
            "bl.last_validated_at, bl.created_by "
            "FROM brain_lessons bl "
            "JOIN brain_lessons_fts fts ON fts.rowid = bl.id "
            "WHERE brain_lessons_fts MATCH ? "
            "ORDER BY bl.confidence DESC LIMIT ?",
            (query.strip(), int(limit)),
        ).fetchall()
    return [_hydrate(r) for r in rows]  # type: ignore[misc]


def record_brain_lesson(
    situation_pattern: str,
    lesson_text: str,
    evidence_decisions: list[int],
    confidence: float = 0.5,
) -> int:
    """Brain-tool entrypoint — same contract as create_lesson but returns just the id.

    This is the function exposed to Brain via the agent-tool dispatcher.
    """
    lesson = create_lesson(
        situation_pattern=situation_pattern,
        lesson_text=lesson_text,
        evidence_decisions=evidence_decisions,
        confidence=confidence,
    )
    return int(lesson["id"])


def mark_validated(lesson_id: int) -> dict | None:
    """Set ``last_validated_at`` to now (UTC). Convenience wrapper."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    return update_lesson(lesson_id, last_validated_at=now)


def _hydrate(row) -> dict | None:
    if row is None:
        return None
    out = dict(row)
    raw = out.pop("evidence_decisions_json", "[]")
    try:
        out["evidence_decisions"] = json.loads(raw) if raw else []
    except (json.JSONDecodeError, TypeError):
        out["evidence_decisions"] = []
    return out
