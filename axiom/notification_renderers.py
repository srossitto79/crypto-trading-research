"""Render compact operator-readable notification text."""

from __future__ import annotations

import re
from typing import Any, Mapping

_TABLE_LINE_RE = re.compile(r"^\s*\|.*\|\s*$")
_SEPARATOR_RE = re.compile(r"^[\s\-|:]+$")
_LEADING_MARKUP_RE = re.compile(r"^\s*(?:#{1,6}\s*|[-*]\s+|\d+\.\s+)")
_DONE_PREFIX_RE = re.compile(r"^\s*done(?:\s*[.:\-\u2013\u2014]\s*|\s+)\s*", re.IGNORECASE)
_SUMMARY_PREFIX_RE = re.compile(
    r"^\s*(?:here(?:'|)s the summary|summary|what i found)\s*:\s*",
    re.IGNORECASE,
)
_AXIOM_SIGNATURE_RE = re.compile(r"\s+(?:-|[\u2013\u2014])\s*Axiom\s*$", re.IGNORECASE)
_NON_SIGNAL_QUESTION_RE = re.compile(
    r"^(?:what would you like me to do|could you|can you|alternatively)\b",
    re.IGNORECASE,
)
_SIGNAL_PREFIX_SCORES = {
    "primary failure cause:": 120,
    "root cause:": 120,
    "failure cause:": 115,
    "closest match:": 110,
    "actual issue:": 105,
    "current state:": 95,
    "result:": 90,
    "summary:": 90,
    "action:": 85,
    "next action:": 85,
}
_SIGNAL_TOKENS = (
    "root cause",
    "failure cause",
    "closest match",
    "needs approval",
    "pending approval",
    "pending approvals",
    "blocked",
    "archived",
    "rejected",
    "approved",
    "no matching record",
    "already listed",
    "updated lessons.md",
)
_SKIP_PREFIXES = (
    "my findings",
    "what i found about",
    "what is in the pipeline right now",
    "the actual issue you should focus on",
    "next steps",
)


def render_discord_message(notification: Mapping[str, Any]) -> str:
    """Render a compact Discord-safe message."""
    event_type = str(notification.get("event_type") or "info").strip().lower()
    title = _single_line(notification.get("title")) or "axiom update"
    summary = _single_line(notification.get("summary"))
    body = _single_line(notification.get("body"))
    raw_summary = notification.get("summary")
    raw_body = notification.get("body")
    metadata = notification.get("metadata") if isinstance(notification.get("metadata"), Mapping) else {}

    if event_type == "approval_required":
        actor = _single_line(metadata.get("actor"))
        target = _single_line(metadata.get("target_id"))
        return _join_lines(
            title,
            f"Actor: {actor}" if actor else summary,
            f"Review in app: /approval{f'#{target}' if target else ''}",
        )
    if event_type == "approval_resolved":
        return _join_lines(title, summary or body, "Review in app: /approval")
    if event_type == "trade_opened":
        return _join_lines(
            title,
            _trade_line(metadata, include_size=True),
            "Inspect in app: /trades",
        )
    if event_type == "trade_closed":
        pnl_line = _single_line(metadata.get("pnl_line")) or summary
        return _join_lines(title, pnl_line or _trade_line(metadata), "Inspect in app: /trades")
    if event_type == "trade_failed":
        return _join_lines(title, summary or body, "Inspect in app: /trades")
    if event_type == "agent_task_failed":
        task_id = _single_line(metadata.get("task_id"))
        agent_id = _single_line(metadata.get("agent_id"))
        return _join_lines(
            title,
            f"Agent: {agent_id}" if agent_id else summary,
            f"Review in app: /tasks{f'#{task_id}' if task_id else ''}",
        )
    if event_type == "agent_task_completed":
        task_id = _single_line(metadata.get("task_id"))
        compact = summarize_discord_text(raw_summary or raw_body) or summary or body
        return _join_lines(title, compact, f"Open in app: /tasks{f'#{task_id}' if task_id else ''}")
    if event_type == "pipeline_transition":
        strategy_id = _single_line(metadata.get("strategy_id"))
        return _join_lines(title, summary or body, f"Open in app: /lab{f'/strategy/{strategy_id}' if strategy_id else ''}")
    if event_type in {"system_degraded", "system_recovered", "risk_critical"}:
        return _join_lines(title, summary or body, "Use /ops for details")
    if event_type == "brain_response":
        compact = summarize_discord_text(raw_summary or raw_body) or summary or body
        return _join_lines(title, compact, "Full context lives in the app")
    return _join_lines(title, summary or body)


def render_discord_thread(notification: Mapping[str, Any]) -> tuple[str, str]:
    """Render digest notifications for threaded delivery."""
    title = _single_line(notification.get("title")) or "axiom digest"
    summary = _single_line(notification.get("summary"))
    body = str(notification.get("body") or "").strip()
    if not body:
        body = summary or title
    message = _join_lines(title, summary or None, body)
    return title[:100], message[:4000]


def summarize_discord_text(text: Any, *, limit: int = 420, max_lines: int = 3) -> str | None:
    """Collapse verbose agent prose into a few high-signal Discord lines."""
    normalized_lines: list[str] = []
    seen: set[str] = set()
    for raw_line in str(text or "").replace("\r", "").replace("```", "").splitlines():
        line = _normalize_discord_line(raw_line)
        if not line:
            continue
        lowered = line.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        normalized_lines.append(line)

    if not normalized_lines:
        fallback = _single_line(text)
        return fallback[:limit] if fallback else None

    scored_lines = [
        (index, line, _discord_line_score(line))
        for index, line in enumerate(normalized_lines)
    ]
    prioritized = [item for item in scored_lines if item[2] > 0]
    if prioritized:
        selected = sorted(prioritized, key=lambda item: (-item[2], item[0]))[: max(1, int(max_lines))]
        selected.sort(key=lambda item: item[0])
        lines = [line for _, line, _ in selected]
    else:
        lines = normalized_lines[: max(1, int(max_lines))]

    compact = "\n".join(lines).strip()
    if len(compact) <= limit:
        return compact

    truncated_lines: list[str] = []
    remaining = max(32, int(limit))
    for line in lines:
        if remaining <= 0:
            break
        newline_cost = 1 if truncated_lines else 0
        if len(line) + newline_cost <= remaining:
            truncated_lines.append(line)
            remaining -= len(line) + newline_cost
            continue
        cutoff = max(24, remaining - newline_cost)
        piece = _truncate_text(line, cutoff)
        if piece:
            truncated_lines.append(piece)
        break
    return "\n".join(truncated_lines).strip()


def _trade_line(metadata: Mapping[str, Any], *, include_size: bool = False) -> str:
    side = _single_line(metadata.get("side")) or _single_line(metadata.get("direction"))
    asset = _single_line(metadata.get("asset"))
    price = _single_line(metadata.get("price"))
    strategy_id = _single_line(metadata.get("strategy_id"))
    parts = []
    if side or asset:
        parts.append(" ".join(part for part in (side, asset) if part))
    if price:
        parts.append(f"@ {price}")
    if include_size:
        size = _single_line(metadata.get("size"))
        if size:
            parts.append(f"Size: {size}")
    if strategy_id:
        parts.append(f"Strategy: {strategy_id}")
    return " | ".join(parts) or "Trade update"


def _join_lines(*parts: str | None) -> str:
    lines = [part.strip() for part in parts if isinstance(part, str) and part.strip()]
    return "\n".join(lines)[:1900]


def _single_line(value: Any) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).strip().split())
    return text[:500] if text else None


def _normalize_discord_line(raw_line: str) -> str | None:
    line = str(raw_line or "").strip()
    if not line or _TABLE_LINE_RE.match(line) or _SEPARATOR_RE.match(line):
        return None
    line = _LEADING_MARKUP_RE.sub("", line)
    line = re.sub(r"\*\*(.*?)\*\*", r"\1", line)
    line = re.sub(r"__(.*?)__", r"\1", line)
    line = re.sub(r"`([^`]+)`", r"\1", line)
    line = _AXIOM_SIGNATURE_RE.sub("", line).strip()
    if not line:
        return None

    line = _DONE_PREFIX_RE.sub("", line)
    line = _SUMMARY_PREFIX_RE.sub("", line)
    line = " ".join(line.split()).strip()
    if not line:
        return None

    lowered = line.lower()
    if lowered in {"Axiom", "app"}:
        return None
    if any(lowered.startswith(prefix) for prefix in _SKIP_PREFIXES):
        return None
    if _NON_SIGNAL_QUESTION_RE.match(lowered):
        return None
    return line


def _discord_line_score(line: str) -> int:
    lowered = line.lower()
    score = 0
    for prefix, prefix_score in _SIGNAL_PREFIX_SCORES.items():
        if lowered.startswith(prefix):
            score = max(score, prefix_score)
            break
    if any(token in lowered for token in _SIGNAL_TOKENS):
        score += 35
    if "approval" in lowered or "approve " in f"{lowered} ":
        score += 20
    if lowered.endswith("?"):
        score -= 80
    if len(line) < 18:
        score -= 20
    elif len(line) > 220:
        score -= 10
    return score


def _truncate_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    trimmed = text[: max(1, limit - 1)].rstrip()
    if " " in trimmed:
        trimmed = trimmed.rsplit(" ", 1)[0]
    return f"{trimmed}..." if trimmed else text[:limit]
