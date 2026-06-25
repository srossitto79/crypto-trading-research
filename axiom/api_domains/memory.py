from __future__ import annotations

import hashlib
import importlib.util
import json
import logging
import re
import subprocess
import sys
import textwrap
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel, Field

from axiom import config as cfg
from axiom.db import get_db
from axiom.vectordb import search_narratives as _search_narratives_sync

log = logging.getLogger("axiom.api")


WORKSPACE_SOURCE = "workspace"
CHROMA_SOURCE = "chroma"
NARRATIVES_SOURCE = "narratives"

DEFAULT_PAGE_LIMIT = 24
MAX_PAGE_LIMIT = 200
DEFAULT_TIMELINE_LIMIT = 18
DEFAULT_CANON_LIMIT = 10
MAX_DAILY_AGENT_SECTIONS = 20
DEFAULT_MAINTENANCE_OLDER_THAN_DAYS = 14
MAX_MAINTENANCE_CANDIDATES = 200

SUPPORTED_SOURCES = (WORKSPACE_SOURCE, CHROMA_SOURCE, NARRATIVES_SOURCE)
CHROMA_COLLECTIONS: tuple[str, ...] = (
    "backtest_results",
    "trade_post_mortems",
    "research_hypotheses",
    "execution_slippage",
    "quant_skills",
    "agent_narratives",
)

CHROMA_COLLECTION_LABELS = {
    "backtest_results": "Backtest Results",
    "trade_post_mortems": "Trade Post-Mortems",
    "research_hypotheses": "Research Hypotheses",
    "execution_slippage": "Execution Slippage",
    "quant_skills": "Quant Skills",
    "agent_narratives": "Agent Narratives",
}

TIER_RANKS = {
    "canon": 3,
    "working": 2,
    "signal": 1,
}

DAILY_AGENT_LOG_RE = re.compile(
    r"^agents/(?P<agent_id>[^/]+)/memory/(?P<date>\d{4}-\d{2}-\d{2})\.md$",
    re.IGNORECASE,
)
HIGH_SIGNAL_DAILY_HEADING_RE = re.compile(
    r"\b(post[- ]?mortem|failure|lesson|blocked|strategy|S\d{4,6}|risk|slippage|deployment)\b",
    re.IGNORECASE,
)


class MemoryTimeRange(BaseModel):
    preset: str | None = Field(default=None, max_length=24)
    from_ts: str | None = Field(default=None, max_length=64)
    to_ts: str | None = Field(default=None, max_length=64)


class MemorySearchRequest(BaseModel):
    query: str | None = Field(default=None, max_length=400)
    sources: list[str] = Field(default_factory=list)
    collections: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    agent_id: str | None = Field(default=None, max_length=128)
    strategy_id: str | None = Field(default=None, max_length=128)
    time_range: MemoryTimeRange | None = None
    include_hidden: bool = False
    limit: int = Field(default=DEFAULT_PAGE_LIMIT, ge=1, le=MAX_PAGE_LIMIT)
    page: int = Field(default=1, ge=1, le=10_000)
    cursor: str | None = Field(default=None, max_length=64)


class MemoryAnnotationBody(BaseModel):
    source_kind: str | None = Field(default=None, max_length=64)
    title_override: str | None = Field(default=None, max_length=240)
    tags: list[str] | None = None
    note: str | None = Field(default=None, max_length=4000)
    tier: str | None = Field(default=None, max_length=64)
    pinned: bool | None = None
    hidden: bool | None = None
    actor: str | None = Field(default="operator", max_length=64)
    item_snapshot: dict[str, Any] | None = None


class MemoryActionBody(BaseModel):
    action: str = Field(min_length=1, max_length=32)
    actor: str | None = Field(default="operator", max_length=64)
    item_snapshot: dict[str, Any] | None = None


class MemoryMaintenanceRequest(BaseModel):
    dry_run: bool = True
    compact_daily_logs: bool = True
    hide_old_daily_logs: bool = True
    archive_narratives: bool = False
    older_than_days: int = Field(default=DEFAULT_MAINTENANCE_OLDER_THAN_DAYS, ge=1, le=3650)
    limit: int = Field(default=MAX_MAINTENANCE_CANDIDATES, ge=1, le=1000)


def _memory_tables_sql() -> str:
    return """
    CREATE TABLE IF NOT EXISTS memory_annotations (
        source TEXT NOT NULL,
        source_id TEXT NOT NULL,
        source_kind TEXT,
        title_override TEXT,
        tags_json TEXT,
        note TEXT,
        tier TEXT,
        pinned INTEGER NOT NULL DEFAULT 0,
        hidden INTEGER NOT NULL DEFAULT 0,
        updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now')),
        PRIMARY KEY (source, source_id)
    );

    CREATE INDEX IF NOT EXISTS idx_memory_annotations_updated_at
        ON memory_annotations (updated_at);
    CREATE INDEX IF NOT EXISTS idx_memory_annotations_pinned
        ON memory_annotations (pinned);
    CREATE INDEX IF NOT EXISTS idx_memory_annotations_hidden
        ON memory_annotations (hidden);

    CREATE TABLE IF NOT EXISTS memory_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source TEXT NOT NULL,
        source_id TEXT NOT NULL,
        action TEXT NOT NULL,
        payload_json TEXT,
        actor TEXT,
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now'))
    );

    CREATE INDEX IF NOT EXISTS idx_memory_events_lookup
        ON memory_events (source, source_id, created_at DESC);
    CREATE INDEX IF NOT EXISTS idx_memory_events_created_at
        ON memory_events (created_at DESC);
    """


def _ensure_memory_tables(conn) -> None:
    conn.executescript(_memory_tables_sql())


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_json_loads(value: Any, default: Any = None) -> Any:
    if value in (None, ""):
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except Exception:
        return default


def _safe_json_dumps(value: Any) -> str:
    try:
        return json.dumps(value, default=str)
    except Exception:
        return json.dumps({"value": str(value)})


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", _normalize_text(value).lower()).strip("-")
    return slug or "item"


def _humanize_filename(path: str) -> str:
    stem = Path(path).stem
    return re.sub(r"[_\\-]+", " ", stem).strip() or stem


def _truncate(value: str, limit: int) -> str:
    text = _normalize_text(value)
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "..."


def _first_content_line(value: str) -> str:
    for line in _normalize_text(value).splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def _hash_text(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8", errors="ignore")).hexdigest()[:16]


def _to_datetime(value: Any) -> datetime | None:
    text = _normalize_text(value)
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def _to_iso(value: Any) -> str | None:
    parsed = _to_datetime(value)
    return parsed.isoformat() if parsed else None


def _datetime_sort_key(value: Any) -> float:
    parsed = _to_datetime(value)
    return parsed.timestamp() if parsed else 0.0


def _normalize_tags(values: Any) -> list[str]:
    if values is None:
        return []
    raw_values: list[str] = []
    if isinstance(values, str):
        raw_values = re.split(r"[,;\n]+", values)
    elif isinstance(values, (list, tuple)):
        raw_values = [str(value or "") for value in values]
    else:
        raw_values = [str(values or "")]

    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in raw_values:
        normalized = re.sub(r"[^a-zA-Z0-9_\\-]+", "-", raw.strip().lower()).strip("-")
        if not normalized or normalized in seen:
            continue
        cleaned.append(normalized)
        seen.add(normalized)
    return cleaned


def _merge_tags(*tag_groups: list[str]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for group in tag_groups:
        for tag in _normalize_tags(group):
            if tag in seen:
                continue
            merged.append(tag)
            seen.add(tag)
    return merged


def _extract_strategy_id(*values: Any) -> str | None:
    for value in values:
        text = _normalize_text(value)
        if not text:
            continue
        match = re.search(r"\bS\d{4,6}\b", text, re.IGNORECASE)
        if match:
            return match.group(0).upper()
    return None


def _extract_agent_id(path_text: str, metadata: dict[str, Any] | None = None) -> str | None:
    if isinstance(metadata, dict):
        for key in ("agent_id", "agent", "agentId"):
            value = _normalize_text(metadata.get(key))
            if value:
                return value
    match = re.search(r"agents[/\\\\]([^/\\\\]+)[/\\\\]memory", path_text, re.IGNORECASE)
    if match:
        return match.group(1)
    return None


def _extract_inline_tags(text: str) -> list[str]:
    return _normalize_tags(re.findall(r"(?<!\w)#([A-Za-z][A-Za-z0-9_\\-]+)", text))


def _clip_item_snapshot(item: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    snapshot = dict(item)
    if "content_preview" in snapshot:
        snapshot["content_preview"] = _truncate(str(snapshot["content_preview"] or ""), 1200)
    if "excerpt" in snapshot:
        snapshot["excerpt"] = _truncate(str(snapshot["excerpt"] or ""), 320)
    if "provenance" in snapshot and isinstance(snapshot["provenance"], dict):
        snapshot["provenance"] = _safe_json_loads(_safe_json_dumps(snapshot["provenance"]), {})
    return snapshot


def _snapshot_signature(snapshot: dict[str, Any] | None) -> str:
    if not isinstance(snapshot, dict):
        return ""
    try:
        return json.dumps(snapshot, default=str, sort_keys=True)
    except Exception:
        return _safe_json_dumps(snapshot)


def _annotation_is_meaningful(annotation: dict[str, Any]) -> bool:
    return bool(
        annotation.get("pinned")
        or annotation.get("hidden")
        or _normalize_text(annotation.get("title_override"))
        or _normalize_text(annotation.get("note"))
        or _normalize_text(annotation.get("tier"))
        or _normalize_tags(annotation.get("tags"))
    )


def _normalize_source_selection(sources: list[str] | None) -> list[str]:
    normalized = []
    seen: set[str] = set()
    for source in sources or []:
        value = _normalize_text(source).lower()
        if value in SUPPORTED_SOURCES and value not in seen:
            normalized.append(value)
            seen.add(value)
    return normalized or list(SUPPORTED_SOURCES)


def _normalize_collection_selection(collections: list[str] | None) -> list[str]:
    normalized = []
    seen: set[str] = set()
    for collection in collections or []:
        value = _normalize_text(collection)
        if value in CHROMA_COLLECTIONS and value not in seen:
            normalized.append(value)
            seen.add(value)
    return normalized or list(CHROMA_COLLECTIONS)


def _resolve_time_range(time_range: MemoryTimeRange | None) -> tuple[datetime | None, datetime | None]:
    if time_range is None:
        return None, None

    from_dt = _to_datetime(time_range.from_ts)
    to_dt = _to_datetime(time_range.to_ts)
    preset = _normalize_text(time_range.preset).lower()
    now = datetime.now(timezone.utc)

    if from_dt or to_dt:
        return from_dt, to_dt
    if preset in {"24h", "1d", "day"}:
        return now - timedelta(days=1), None
    if preset in {"7d", "week"}:
        return now - timedelta(days=7), None
    if preset in {"30d", "month"}:
        return now - timedelta(days=30), None
    if preset in {"90d", "quarter"}:
        return now - timedelta(days=90), None
    if preset in {"365d", "1y", "year"}:
        return now - timedelta(days=365), None
    return None, None


def _empty_nav_indicator() -> dict[str, Any]:
    return {
        "kind": "none",
        "severity": "neutral",
        "label": "",
        "summary": "",
        "count": 0,
        "seen_key": "",
    }


def _build_nav_indicator(
    kind: str,
    severity: str,
    label: str,
    summary: str,
    seen_key: str,
    *,
    count: int = 0,
) -> dict[str, Any]:
    return {
        "kind": kind,
        "severity": severity,
        "label": label,
        "summary": summary,
        "count": count,
        "seen_key": seen_key,
    }


def _workspace_roots() -> list[Path]:
    roots: list[Path] = []
    seen: set[str] = set()
    candidates = [
        getattr(cfg, "WORKSPACE_DIR", None),
        getattr(cfg, "LEGACY_WORKSPACE_DIR", None),
    ]
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate)
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        if path.exists():
            roots.append(path)
    if not roots:
        roots.append(Path(getattr(cfg, "WORKSPACE_DIR", Path.cwd() / "workspace")))
    return roots


def _workspace_targets(root: Path) -> list[Path]:
    targets: list[Path] = []
    memory_root = root / "memory"
    agents_root = root / "agents"

    if memory_root.exists():
        targets.extend(path for path in sorted(memory_root.rglob("*.md")) if path.is_file())

    if agents_root.exists():
        for path in sorted(agents_root.glob("*/memory/**/*.md")):
            if path.is_file():
                targets.append(path)
        for path in sorted(agents_root.glob("*/memory/*.md")):
            if path.is_file():
                targets.append(path)

    return targets


def _discover_workspace_files() -> list[tuple[Path, str]]:
    files_by_relative: dict[str, tuple[Path, float]] = {}
    for root in _workspace_roots():
        for path in _workspace_targets(root):
            try:
                relative_path = str(path.relative_to(root)).replace("\\", "/")
                mtime = path.stat().st_mtime
            except Exception:
                continue
            current = files_by_relative.get(relative_path)
            if current is None or mtime >= current[1]:
                files_by_relative[relative_path] = (path, mtime)
    return [(path, relative_path) for relative_path, (path, _mtime) in files_by_relative.items()]


def _resolve_workspace_file(relative_path: str) -> Path | None:
    normalized = _normalize_text(relative_path).replace("\\", "/").lstrip("/")
    if not normalized or ".." in Path(normalized).parts:
        return None
    for root in _workspace_roots():
        candidate = root / normalized
        try:
            candidate.relative_to(root)
        except ValueError:
            continue
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def _primary_workspace_root() -> Path:
    roots = _workspace_roots()
    return roots[0] if roots else Path(getattr(cfg, "WORKSPACE_DIR", Path.cwd() / "workspace"))


def _daily_agent_log_match(relative_path: str) -> re.Match[str] | None:
    return DAILY_AGENT_LOG_RE.match(_normalize_text(relative_path).replace("\\", "/"))


def _daily_agent_log_source_path(source_id: str) -> str:
    return _normalize_text(source_id).split("#", 1)[0].replace("\\", "/")


def _daily_agent_log_title(relative_path: str) -> str:
    match = _daily_agent_log_match(relative_path)
    if not match:
        return _humanize_filename(relative_path)
    agent_id = match.group("agent_id")
    date_text = match.group("date")
    return f"{agent_id} Daily Log {date_text}"


def _is_high_signal_daily_heading(title: str, body: str) -> bool:
    if HIGH_SIGNAL_DAILY_HEADING_RE.search(title):
        return True
    return bool(re.search(r"\bS\d{4,6}\b", body, re.IGNORECASE))


def _daily_agent_log_date(relative_path_or_source_id: str) -> datetime | None:
    match = _daily_agent_log_match(_daily_agent_log_source_path(relative_path_or_source_id))
    if not match:
        return None
    try:
        parsed = datetime.fromisoformat(match.group("date"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc)


def _daily_agent_summary_relative_path(relative_path_or_source_id: str) -> str | None:
    source_path = _daily_agent_log_source_path(relative_path_or_source_id)
    match = _daily_agent_log_match(source_path)
    if not match:
        return None
    return f"agents/{match.group('agent_id')}/memory/summaries/{match.group('date')}.md"


def _daily_agent_summary_path(relative_path_or_source_id: str) -> Path | None:
    relative = _daily_agent_summary_relative_path(relative_path_or_source_id)
    if not relative:
        return None
    return _primary_workspace_root() / relative


def _daily_agent_log_summary(relative_path: str, content: str) -> str:
    source_path = _daily_agent_log_source_path(relative_path)
    match = _daily_agent_log_match(source_path)
    agent_id = match.group("agent_id") if match else _extract_agent_id(source_path) or "unknown"
    date_text = match.group("date") if match else Path(source_path).stem
    sections = _parse_daily_agent_log_sections(source_path, content)
    signal_sections = [section for section in sections if section.get("anchor")]
    strategy_ids = sorted(set(_extract_strategy_id(content) for content in [content]) - {None})
    inline_tags = _extract_inline_tags(content)
    signal_titles = [_normalize_text(section.get("title")) for section in signal_sections]
    signal_titles = [title for title in signal_titles if title][:MAX_DAILY_AGENT_SECTIONS]
    first_line = _first_content_line(content)

    lines = [
        f"# {agent_id} Daily Summary {date_text}",
        "",
        "## Source",
        f"- Raw log: `{source_path}`",
        f"- Agent: `{agent_id}`",
        f"- Date: `{date_text}`",
        f"- Raw characters: {len(content)}",
        f"- High-signal sections retained: {len(signal_sections)}",
        "",
        "## Digest",
        f"- {first_line or 'No non-empty daily log content.'}",
    ]

    if strategy_ids:
        lines.extend(["", "## Strategy IDs"])
        lines.extend(f"- `{strategy_id}`" for strategy_id in strategy_ids[:40])

    if inline_tags:
        lines.extend(["", "## Tags"])
        lines.extend(f"- `{tag}`" for tag in inline_tags[:40])

    if signal_titles:
        lines.extend(["", "## High-Signal Sections"])
        lines.extend(f"- {title}" for title in signal_titles)

    lines.extend(
        [
            "",
            "## Maintenance",
            "- Generated deterministically from the raw daily log.",
            "- Raw source file is preserved.",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def _parse_daily_agent_log_sections(relative_path: str, content: str) -> list[dict[str, str]]:
    stripped = _normalize_text(content)
    if not stripped:
        return []

    sections: list[dict[str, str]] = [
        {
            "anchor": "",
            "title": _daily_agent_log_title(relative_path),
            "content": stripped,
        }
    ]
    matches = list(re.finditer(r"(?m)^(#{1,6})\s+(.*?)\s*$", content))
    signal_sections = 0

    for index, match in enumerate(matches):
        title = _normalize_text(match.group(2)) or _daily_agent_log_title(relative_path)
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(content)
        body = content[start:end].strip()
        section_text = body or title
        if not _is_high_signal_daily_heading(title, section_text):
            continue
        sections.append({"anchor": _slugify(title), "title": title, "content": section_text})
        signal_sections += 1
        if signal_sections >= MAX_DAILY_AGENT_SECTIONS:
            break

    return sections


def _parse_markdown_sections(relative_path: str, content: str) -> list[dict[str, str]]:
    if _daily_agent_log_match(relative_path):
        return _parse_daily_agent_log_sections(relative_path, content)

    matches = list(re.finditer(r"(?m)^(#{1,6})\s+(.*?)\s*$", content))
    sections: list[dict[str, str]] = []
    default_title = _humanize_filename(relative_path)

    if not matches:
        stripped = _normalize_text(content)
        if stripped:
            sections.append({"anchor": "", "title": default_title, "content": stripped})
        return sections

    preamble = content[: matches[0].start()].strip()
    if preamble:
        sections.append({"anchor": "intro", "title": default_title, "content": preamble})

    for index, match in enumerate(matches):
        title = _normalize_text(match.group(2)) or default_title
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(content)
        body = content[start:end].strip()
        section_text = body or title
        sections.append({"anchor": _slugify(title), "title": title, "content": section_text})

    return sections


def _workspace_item_tags(relative_path: str, title: str, content: str, agent_id: str | None) -> list[str]:
    tags: list[str] = []
    lower_path = relative_path.lower()
    lower_title = title.lower()
    if relative_path.endswith("/MEMORY.md") or relative_path.lower().endswith("/memory.md"):
        tags.append("canon")
    if re.search(r"\b\d{4}-\d{2}-\d{2}\b", relative_path):
        tags.append("daily")
    if "postmortem" in lower_path or "post_mortem" in lower_path or "postmortem" in lower_title:
        tags.append("postmortem")
    if "lesson" in lower_path or "lesson" in lower_title:
        tags.append("lesson")
    if "failure" in lower_path or "failure" in lower_title:
        tags.append("failure")
    if "idea" in lower_path or "ideation" in lower_path:
        tags.append("ideation")
    if agent_id:
        tags.append(agent_id)
    return _merge_tags(tags, _extract_inline_tags(content))


def _normalize_workspace_item(
    *,
    absolute_path: Path,
    relative_path: str,
    title: str,
    content: str,
    anchor: str,
) -> dict[str, Any]:
    try:
        stat = absolute_path.stat()
        created_at = datetime.fromtimestamp(stat.st_ctime, tz=timezone.utc).isoformat()
        updated_at = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()
    except Exception:
        created_at = None
        updated_at = None

    source_id = relative_path if not anchor else f"{relative_path}#{anchor}"
    agent_id = _extract_agent_id(relative_path)
    strategy_id = _extract_strategy_id(relative_path, title, content)
    tags = _workspace_item_tags(relative_path, title, content, agent_id)
    excerpt = _truncate(_first_content_line(content) or title, 180)

    return {
        "source": WORKSPACE_SOURCE,
        "source_kind": "workspace_markdown",
        "source_id": source_id,
        "title": title,
        "excerpt": excerpt,
        "content_preview": _truncate(content, 1400),
        "created_at": created_at,
        "updated_at": updated_at,
        "score": 0.0,
        "agent_id": agent_id,
        "strategy_id": strategy_id,
        "collection": "workspace_memory",
        "tags": tags,
        "tier": None,
        "pinned": False,
        "hidden": False,
        "note": None,
        "provenance": {
            "relative_path": relative_path,
            "absolute_path": str(absolute_path),
            "heading": title if anchor else None,
            "anchor": anchor or None,
        },
        "actions": ["annotate", "hide", "unhide"],
    }


def _workspace_items() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for absolute_path, relative_path in _discover_workspace_files():
        try:
            content = absolute_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        for section in _parse_markdown_sections(relative_path, content):
            body = _normalize_text(section.get("content"))
            if not body:
                continue
            items.append(
                _normalize_workspace_item(
                    absolute_path=absolute_path,
                    relative_path=relative_path,
                    title=_normalize_text(section.get("title")) or _humanize_filename(relative_path),
                    content=body,
                    anchor=_normalize_text(section.get("anchor")),
                )
            )

    items.sort(
        key=lambda item: (
            _datetime_sort_key(item.get("updated_at") or item.get("created_at")),
            item.get("source_id") or "",
        ),
        reverse=True,
    )
    return items


def _workspace_health() -> dict[str, Any]:
    files = _discover_workspace_files()
    latest_updated = None
    for path, _relative in files:
        try:
            updated = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()
        except Exception:
            continue
        if latest_updated is None or _datetime_sort_key(updated) > _datetime_sort_key(latest_updated):
            latest_updated = updated

    return {
        "source": WORKSPACE_SOURCE,
        "configured": True,
        "healthy": True,
        "status": "active",
        "summary": f"{len(files)} workspace memory file{'s' if len(files) != 1 else ''}",
        "count": len(files),
        "latest_updated_at": latest_updated,
        "collections": ["workspace_memory"],
    }


def _chroma_dir() -> Path:
    return Path(getattr(cfg, "AXIOM_HOME", Path.home() / ".Axiom")) / "chromadb"


def _run_chroma_subprocess(payload: dict[str, Any], timeout: int = 60) -> dict[str, Any]:
    chroma_dir = _chroma_dir()
    chroma_dir.mkdir(parents=True, exist_ok=True)

    script = textwrap.dedent(
        """
        import json
        import pathlib
        import sys

        payload = json.loads(sys.stdin.read())
        chroma_path = pathlib.Path(payload["path"])

        try:
            import chromadb
        except Exception as exc:
            print(json.dumps({"ok": False, "error": f"chromadb import failed: {exc}"}))
            raise SystemExit(0)

        try:
            client = chromadb.PersistentClient(path=str(chroma_path))
            existing = set()
            try:
                for collection in client.list_collections():
                    existing.add(getattr(collection, "name", str(collection)))
            except Exception:
                pass

            mode = str(payload.get("mode") or "query").strip().lower()
            collections = payload.get("collections") or []
            limit = max(1, int(payload.get("limit") or 10))
            query = str(payload.get("query") or "").strip()

            def flatten_query(result):
                items = []
                ids = (result or {}).get("ids") or []
                if not ids:
                    return items
                documents = (result or {}).get("documents") or [[]]
                metadatas = (result or {}).get("metadatas") or [[]]
                distances = (result or {}).get("distances") or [[]]
                for index, doc_id in enumerate(ids[0]):
                    items.append({
                        "id": doc_id,
                        "document": documents[0][index] if documents and documents[0] else "",
                        "metadata": metadatas[0][index] if metadatas and metadatas[0] else {},
                        "distance": distances[0][index] if distances and distances[0] else None,
                    })
                return items

            def flatten_get(result):
                items = []
                ids = (result or {}).get("ids") or []
                documents = (result or {}).get("documents") or []
                metadatas = (result or {}).get("metadatas") or []
                for index, doc_id in enumerate(ids):
                    items.append({
                        "id": doc_id,
                        "document": documents[index] if index < len(documents) else "",
                        "metadata": metadatas[index] if index < len(metadatas) else {},
                    })
                return items

            if mode == "get":
                collection_name = str(payload.get("collection") or "").strip()
                doc_id = str(payload.get("doc_id") or "").strip()
                if collection_name not in existing or not doc_id:
                    print(json.dumps({"ok": True, "item": None}))
                    raise SystemExit(0)
                collection = client.get_collection(collection_name)
                result = collection.get(ids=[doc_id], include=["documents", "metadatas"])
                items = flatten_get(result)
                item = items[0] if items else None
                print(json.dumps({"ok": True, "item": item, "collection": collection_name}))
                raise SystemExit(0)

            response = {"ok": True, "collections": {}}
            for collection_name in collections:
                if collection_name not in existing:
                    response["collections"][collection_name] = {
                        "exists": False,
                        "count": 0,
                        "items": [],
                    }
                    continue

                collection = client.get_collection(collection_name)
                try:
                    count = int(collection.count())
                except Exception:
                    count = 0

                if mode == "stats":
                    items = []
                    if count > 0:
                        result = collection.get(limit=min(limit, count), include=["documents", "metadatas"])
                        items = flatten_get(result)
                elif query:
                    items = []
                    if count > 0:
                        result = collection.query(query_texts=[query], n_results=min(limit, count))
                        items = flatten_query(result)
                else:
                    items = []
                    if count > 0:
                        result = collection.get(limit=min(limit, count), include=["documents", "metadatas"])
                        items = flatten_get(result)

                response["collections"][collection_name] = {
                    "exists": True,
                    "count": count,
                    "items": items,
                }

            print(json.dumps(response, default=str))
        except Exception as exc:
            print(json.dumps({"ok": False, "error": str(exc)}))
        """
    )

    try:
        proc = subprocess.run(
            [sys.executable, "-c", script],
            input=json.dumps({**payload, "path": str(chroma_dir)}),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except Exception as exc:
        return {"ok": False, "error": str(exc)}

    if proc.returncode != 0:
        return {
            "ok": False,
            "error": _truncate((proc.stderr or proc.stdout or "").strip() or f"exit {proc.returncode}", 400),
        }

    try:
        return json.loads(proc.stdout or "{}")
    except Exception:
        return {"ok": False, "error": _truncate(proc.stdout or "invalid chroma response", 400)}


def _collection_title(collection: str, metadata: dict[str, Any], document: str, doc_id: str) -> str:
    if collection == "backtest_results":
        strategy_name = _normalize_text(metadata.get("strategy_name") or metadata.get("strategy_id"))
        asset = _normalize_text(metadata.get("asset") or metadata.get("symbol"))
        if strategy_name and asset:
            return f"{strategy_name} on {asset}"
        if strategy_name:
            return strategy_name
    if collection == "trade_post_mortems":
        strategy_name = _normalize_text(metadata.get("strategy") or metadata.get("strategy_name"))
        asset = _normalize_text(metadata.get("asset"))
        if strategy_name and asset:
            return f"Post-mortem: {strategy_name} on {asset}"
    if collection == "execution_slippage":
        strategy_name = _normalize_text(metadata.get("strategy") or metadata.get("strategy_name"))
        asset = _normalize_text(metadata.get("asset"))
        leg = _normalize_text(metadata.get("leg"))
        parts = [part for part in [strategy_name, asset, leg] if part]
        if parts:
            return " | ".join(parts)
    if collection == "research_hypotheses":
        return _first_content_line(document) or _normalize_text(metadata.get("title")) or "Research Hypothesis"
    return _first_content_line(document) or _normalize_text(metadata.get("title")) or doc_id


def _chroma_item_tags(collection: str, metadata: dict[str, Any], document: str) -> list[str]:
    tags = [collection]
    for key in ("strategy_type", "timeframe", "outcome", "direction", "leg", "type", "source"):
        value = _normalize_text(metadata.get(key))
        if value:
            tags.append(value)
    strategy_id = _extract_strategy_id(
        metadata.get("lifecycle_strategy_id"),
        metadata.get("strategy_id"),
        document,
    )
    if strategy_id:
        tags.append(strategy_id.lower())
    return _merge_tags(tags, _extract_inline_tags(document))


def _normalize_chroma_record(collection: str, record: dict[str, Any], query: str = "", rank: int = 0) -> dict[str, Any]:
    metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
    document = _normalize_text(record.get("document"))
    doc_id = _normalize_text(record.get("id")) or _hash_text(document or json.dumps(metadata, default=str))
    distance = record.get("distance")
    try:
        distance_value = float(distance)
    except Exception:
        distance_value = None

    score = 0.0
    if query and distance_value is not None:
        score = max(0.0, 1.0 / (1.0 + max(0.0, distance_value)))
    elif query:
        score = max(0.1, 0.9 - (rank * 0.05))
    else:
        score = max(0.05, 0.35 - (rank * 0.01))

    source_id = f"{collection}:{doc_id}"
    strategy_id = _extract_strategy_id(
        metadata.get("lifecycle_strategy_id"),
        metadata.get("strategy_id"),
        metadata.get("strategy_name"),
        doc_id,
        document,
    )
    agent_id = _extract_agent_id(collection, metadata)
    title = _collection_title(collection, metadata, document, doc_id)
    excerpt = _truncate(_first_content_line(document) or title, 180)

    created_at = _to_iso(
        metadata.get("recorded_at")
        or metadata.get("created_at")
        or metadata.get("start_date")
        or metadata.get("evaluation_start_date")
    )
    updated_at = _to_iso(
        metadata.get("recorded_at")
        or metadata.get("updated_at")
        or metadata.get("end_date")
        or metadata.get("evaluation_end_date")
        or created_at
    )

    return {
        "source": CHROMA_SOURCE,
        "source_kind": "vector_record",
        "source_id": source_id,
        "title": title,
        "excerpt": excerpt,
        "content_preview": _truncate(document or title, 1400),
        "created_at": created_at,
        "updated_at": updated_at,
        "score": score,
        "agent_id": agent_id,
        "strategy_id": strategy_id,
        "collection": collection,
        "tags": _chroma_item_tags(collection, metadata, document),
        "tier": None,
        "pinned": False,
        "hidden": False,
        "note": None,
        "provenance": {
            "collection": collection,
            "doc_id": doc_id,
            "distance": distance_value,
            "metadata": metadata,
        },
        "actions": ["annotate", "hide", "unhide"],
    }


def _browse_chroma_items(collections: list[str], limit: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    payload = _run_chroma_subprocess(
        {
            "mode": "browse",
            "collections": collections,
            "limit": max(1, min(limit, 50)),
        }
    )
    items: list[dict[str, Any]] = []
    stats = {"ok": bool(payload.get("ok")), "error": payload.get("error"), "collections": {}}
    for collection in collections:
        entry = (payload.get("collections") or {}).get(collection, {})
        stats["collections"][collection] = {
            "exists": bool(entry.get("exists")),
            "count": int(entry.get("count") or 0),
        }
        for index, record in enumerate(entry.get("items") or []):
            items.append(_normalize_chroma_record(collection, record, "", index))
    return items, stats


def _search_chroma_items(query: str, collections: list[str], limit: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    payload = _run_chroma_subprocess(
        {
            "mode": "query",
            "query": query,
            "collections": collections,
            "limit": max(1, min(limit, 50)),
        }
    )
    items: list[dict[str, Any]] = []
    stats = {"ok": bool(payload.get("ok")), "error": payload.get("error"), "collections": {}}
    for collection in collections:
        entry = (payload.get("collections") or {}).get(collection, {})
        stats["collections"][collection] = {
            "exists": bool(entry.get("exists")),
            "count": int(entry.get("count") or 0),
        }
        for index, record in enumerate(entry.get("items") or []):
            items.append(_normalize_chroma_record(collection, record, query, index))
    return items, stats


def _load_chroma_item(source_id: str) -> dict[str, Any] | None:
    if ":" not in source_id:
        return None
    collection, doc_id = source_id.split(":", 1)
    if collection not in CHROMA_COLLECTIONS or not _normalize_text(doc_id):
        return None
    payload = _run_chroma_subprocess(
        {
            "mode": "get",
            "collection": collection,
            "doc_id": doc_id,
        }
    )
    if not payload.get("ok") or not isinstance(payload.get("item"), dict):
        return None
    return _normalize_chroma_record(collection, payload["item"])


def _chroma_health(limit: int = 6) -> dict[str, Any]:
    from axiom.vectordb import _in_process_chroma_disabled

    if _in_process_chroma_disabled():
        # The vector layer is off on this host (AXIOM_DISABLE_CHROMA_IN_PROCESS,
        # an ONNX-segfault guard) — report it honestly instead of probing the
        # out-of-process subprocess and showing a misleading "active / no records
        # yet". Flagged so the overview filters this dead row out of the UI.
        return {
            "source": CHROMA_SOURCE,
            "configured": False,
            "healthy": False,
            "status": "disabled",
            "vector_layer_disabled": True,
            "summary": "Vector memory disabled on this host (AXIOM_DISABLE_CHROMA_IN_PROCESS)",
            "count": 0,
            "collections": [],
            "latest_updated_at": None,
        }
    payload = _run_chroma_subprocess(
        {
            "mode": "stats",
            "collections": list(CHROMA_COLLECTIONS),
            "limit": max(1, min(limit, 12)),
        }
    )
    collections = payload.get("collections") if isinstance(payload.get("collections"), dict) else {}
    total = 0
    available = 0
    for collection in CHROMA_COLLECTIONS:
        entry = collections.get(collection, {})
        count = int(entry.get("count") or 0)
        total += max(0, count)
        if entry.get("exists"):
            available += 1

    return {
        "source": CHROMA_SOURCE,
        "configured": importlib.util.find_spec("chromadb") is not None,
        "healthy": bool(payload.get("ok")),
        "status": "active" if payload.get("ok") else "degraded",
        "summary": (
            f"{available}/{len(CHROMA_COLLECTIONS)} collections online"
            if payload.get("ok")
            else _truncate(_normalize_text(payload.get("error")) or "Chroma unavailable", 160)
        ),
        "count": total,
        "collections": [
            {
                "name": collection,
                "label": CHROMA_COLLECTION_LABELS.get(collection, collection),
                "count": int((collections.get(collection, {}) or {}).get("count") or 0),
                "exists": bool((collections.get(collection, {}) or {}).get("exists")),
            }
            for collection in CHROMA_COLLECTIONS
        ],
        "latest_updated_at": None,
    }


def _normalize_narrative_item(raw: dict[str, Any], rank: int = 0) -> dict[str, Any]:
    metadata = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
    content = _normalize_text(
        raw.get("memory")
        or raw.get("content")
        or raw.get("text")
        or raw.get("document")
        or raw.get("snippet")
        or metadata.get("content")
    )
    raw_id = _normalize_text(
        raw.get("id")
        or raw.get("memory_id")
        or raw.get("memoryId")
        or raw.get("document_id")
        or raw.get("documentId")
    )
    synthetic_id = not bool(raw_id)
    source_id = raw_id or f"cached-{_hash_text(content or _safe_json_dumps(metadata))}"
    title = (
        _normalize_text(raw.get("title"))
        or _normalize_text(metadata.get("title"))
        or _first_content_line(content)
        or "Narrative entry"
    )
    excerpt = _truncate(_first_content_line(content) or title, 180)

    score = 0.0
    for candidate in (raw.get("score"), raw.get("relevance"), raw.get("similarity"), raw.get("rank")):
        try:
            score = float(candidate)
            break
        except Exception:
            continue
    if score <= 0:
        score = max(0.15, 0.95 - (rank * 0.05))

    created_at = _to_iso(
        raw.get("created_at")
        or raw.get("createdAt")
        or metadata.get("created_at")
        or metadata.get("createdAt")
    )
    updated_at = _to_iso(
        raw.get("updated_at")
        or raw.get("updatedAt")
        or metadata.get("updated_at")
        or metadata.get("updatedAt")
        or created_at
    )
    strategy_id = _extract_strategy_id(
        metadata.get("strategy_id"),
        metadata.get("strategy"),
        title,
        content,
    )
    agent_id = _extract_agent_id(NARRATIVES_SOURCE, metadata)
    collection = _normalize_text(metadata.get("type")) or "narratives"
    tags = _merge_tags(
        _normalize_tags(metadata.get("tags")),
        _normalize_tags([collection, metadata.get("source")]),
        _extract_inline_tags(content),
    )
    actions = ["annotate", "hide", "unhide"]

    return {
        "source": NARRATIVES_SOURCE,
        "source_kind": "narrative",
        "source_id": source_id,
        "title": title,
        "excerpt": excerpt,
        "content_preview": _truncate(content or title, 1400),
        "created_at": created_at,
        "updated_at": updated_at,
        "score": score,
        "agent_id": agent_id,
        "strategy_id": strategy_id,
        "collection": collection,
        "tags": tags,
        "tier": None,
        "pinned": False,
        "hidden": False,
        "note": None,
        "provenance": {
            "metadata": metadata,
            "remote_id": None if synthetic_id else raw_id,
        },
        "actions": actions,
    }


def _normalize_narrative_results(results: list[dict[str, Any]] | None, *, browse_mode: bool = False) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for index, raw in enumerate(results or []):
        if not isinstance(raw, dict):
            continue
        item = _normalize_narrative_item(raw, index)
        if browse_mode:
            item["score"] = 0.0
        items.append(item)

    for item in items:
        _remember_item_snapshot(item)
    return items


def _narrative_query_results(query: str, limit: int) -> list[dict]:
    raw = _search_narratives_sync(query, n_results=max(1, min(limit, 12)))
    return [{"content": r.get("document", ""), "id": r.get("id", "")} for r in raw]


async def _search_narrative_items(query: str, limit: int) -> list[dict[str, Any]]:
    try:
        results = _narrative_query_results(query, limit)
    except Exception:
        return []
    return _normalize_narrative_results(results)


async def _browse_narrative_items(limit: int) -> list[dict[str, Any]]:
    try:
        results = _narrative_query_results("*", limit)
    except Exception:
        return []
    return _normalize_narrative_results(results, browse_mode=True)


def _browse_narrative_items_sync(limit: int) -> list[dict[str, Any]]:
    try:
        results = _narrative_query_results("*", limit)
    except Exception:
        return []
    return _normalize_narrative_results(results, browse_mode=True)


def _narratives_health() -> dict[str, Any]:
    from axiom.vectordb import _in_process_chroma_disabled

    if _in_process_chroma_disabled():
        # Narrative recall is ChromaDB-backed; when the vector layer is off it is
        # a no-op. Flag it so the overview drops this dead row.
        return {
            "source": NARRATIVES_SOURCE,
            "configured": False,
            "healthy": False,
            "status": "disabled",
            "vector_layer_disabled": True,
            "summary": "Narrative recall disabled (vector layer off)",
            "count": 0,
            "latest_updated_at": None,
            "collections": ["narratives"],
            "config_source": "chromadb",
        }
    return {
        "source": NARRATIVES_SOURCE,
        "configured": True,
        "healthy": True,
        "status": "active",
        "summary": "Narrative recall backed by local ChromaDB",
        "count": 0,
        "latest_updated_at": None,
        "collections": ["narratives"],
        "config_source": "chromadb",
    }


def _load_annotations_map() -> dict[tuple[str, str], dict[str, Any]]:
    with get_db() as conn:
        _ensure_memory_tables(conn)
        rows = conn.execute(
            """
            SELECT source, source_id, source_kind, title_override, tags_json, note, tier, pinned, hidden, updated_at
            FROM memory_annotations
            """
        ).fetchall()

    annotations: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        annotation = {
            "source": _normalize_text(row["source"]),
            "source_id": _normalize_text(row["source_id"]),
            "source_kind": _normalize_text(row["source_kind"]) or None,
            "title_override": _normalize_text(row["title_override"]) or None,
            "tags": _normalize_tags(_safe_json_loads(row["tags_json"], [])),
            "note": _normalize_text(row["note"]) or None,
            "tier": _normalize_text(row["tier"]) or None,
            "pinned": bool(row["pinned"]),
            "hidden": bool(row["hidden"]),
            "updated_at": _to_iso(row["updated_at"]) or row["updated_at"],
        }
        annotations[(annotation["source"], annotation["source_id"])] = annotation
    return annotations


def _get_annotation(source: str, source_id: str) -> dict[str, Any] | None:
    with get_db() as conn:
        _ensure_memory_tables(conn)
        row = conn.execute(
            """
            SELECT source, source_id, source_kind, title_override, tags_json, note, tier, pinned, hidden, updated_at
            FROM memory_annotations
            WHERE source = ? AND source_id = ?
            LIMIT 1
            """,
            (source, source_id),
        ).fetchone()
    if row is None:
        return None
    return {
        "source": _normalize_text(row["source"]),
        "source_id": _normalize_text(row["source_id"]),
        "source_kind": _normalize_text(row["source_kind"]) or None,
        "title_override": _normalize_text(row["title_override"]) or None,
        "tags": _normalize_tags(_safe_json_loads(row["tags_json"], [])),
        "note": _normalize_text(row["note"]) or None,
        "tier": _normalize_text(row["tier"]) or None,
        "pinned": bool(row["pinned"]),
        "hidden": bool(row["hidden"]),
        "updated_at": _to_iso(row["updated_at"]) or row["updated_at"],
    }


def _record_memory_event(
    *,
    source: str,
    source_id: str,
    action: str,
    actor: str | None,
    payload: dict[str, Any] | None = None,
) -> None:
    with get_db() as conn:
        _ensure_memory_tables(conn)
        conn.execute(
            """
            INSERT INTO memory_events (source, source_id, action, payload_json, actor, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                source,
                source_id,
                _normalize_text(action) or "event",
                _safe_json_dumps(payload or {}),
                _normalize_text(actor) or "operator",
                _now_iso(),
            ),
        )


def _load_memory_events(source: str, source_id: str, limit: int = 20) -> list[dict[str, Any]]:
    with get_db() as conn:
        _ensure_memory_tables(conn)
        rows = conn.execute(
            """
            SELECT id, source, source_id, action, payload_json, actor, created_at
            FROM memory_events
            WHERE source = ? AND source_id = ? AND action <> 'snapshot'
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (source, source_id, max(1, min(limit, 100))),
        ).fetchall()

    events: list[dict[str, Any]] = []
    for row in rows:
        payload = _safe_json_loads(row["payload_json"], {})
        if not isinstance(payload, dict):
            payload = {}
        events.append(
            {
                "id": int(row["id"]),
                "source": _normalize_text(row["source"]),
                "source_id": _normalize_text(row["source_id"]),
                "action": _normalize_text(row["action"]) or "event",
                "actor": _normalize_text(row["actor"]) or "operator",
                "created_at": _to_iso(row["created_at"]) or row["created_at"],
                "payload": payload,
            }
        )
    return events


def _latest_item_snapshot(source: str, source_id: str) -> dict[str, Any] | None:
    with get_db() as conn:
        _ensure_memory_tables(conn)
        rows = conn.execute(
            """
            SELECT payload_json
            FROM memory_events
            WHERE source = ? AND source_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT 40
            """,
            (source, source_id),
        ).fetchall()

    for row in rows:
        payload = _safe_json_loads(row["payload_json"], {})
        if not isinstance(payload, dict):
            continue
        snapshot = payload.get("snapshot")
        if isinstance(snapshot, dict):
            return dict(snapshot)
    return None


def _remember_item_snapshot(item: dict[str, Any], *, actor: str = "system") -> None:
    snapshot = _clip_item_snapshot(item)
    if not snapshot:
        return

    source = _normalize_text(snapshot.get("source")).lower()
    source_id = _normalize_text(snapshot.get("source_id"))
    if source not in SUPPORTED_SOURCES or not source_id:
        return

    existing = _latest_item_snapshot(source, source_id)
    if _snapshot_signature(existing) == _snapshot_signature(snapshot):
        return

    _record_memory_event(
        source=source,
        source_id=source_id,
        action="snapshot",
        actor=actor,
        payload={
            "summary": "Snapshot refreshed",
            "snapshot": snapshot,
        },
    )


def _apply_annotation(item: dict[str, Any], annotation: dict[str, Any] | None) -> dict[str, Any]:
    applied = dict(item)
    if not annotation:
        return applied

    if annotation.get("source_kind"):
        applied["source_kind"] = annotation.get("source_kind")
    if annotation.get("title_override"):
        applied["title"] = annotation.get("title_override")
    applied["tags"] = _merge_tags(_normalize_tags(item.get("tags")), _normalize_tags(annotation.get("tags")))
    applied["tier"] = annotation.get("tier") or item.get("tier")
    applied["pinned"] = bool(annotation.get("pinned"))
    applied["hidden"] = bool(annotation.get("hidden"))
    applied["note"] = annotation.get("note") or item.get("note")
    applied["annotation_updated_at"] = annotation.get("updated_at")
    return applied


def _annotated_source_record(source: str, source_id: str, annotation: dict[str, Any]) -> dict[str, Any]:
    snapshot = _latest_item_snapshot(source, source_id) or {
        "source": source,
        "source_kind": annotation.get("source_kind")
        or ("workspace_markdown" if source == WORKSPACE_SOURCE else "vector_record" if source == CHROMA_SOURCE else "remote_memory"),
        "source_id": source_id,
        "title": annotation.get("title_override") or source_id,
        "excerpt": annotation.get("note") or annotation.get("title_override") or source_id,
        "content_preview": annotation.get("note") or annotation.get("title_override") or source_id,
        "created_at": annotation.get("updated_at"),
        "updated_at": annotation.get("updated_at"),
        "score": 0.0,
        "agent_id": None,
        "strategy_id": _extract_strategy_id(source_id),
        "collection": "narratives" if source == NARRATIVES_SOURCE else "workspace_memory",
        "tags": annotation.get("tags") or [],
        "tier": None,
        "pinned": False,
        "hidden": False,
        "note": None,
        "provenance": {"cached_only": True},
        "actions": ["annotate", "hide", "unhide"],
    }
    snapshot["source"] = source
    snapshot["source_id"] = source_id
    return _apply_annotation(snapshot, annotation)


def _cached_narrative_items(
    annotations: dict[tuple[str, str], dict[str, Any]],
    *,
    pinned_only: bool = False,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for (source, source_id), annotation in annotations.items():
        if source != NARRATIVES_SOURCE or not _annotation_is_meaningful(annotation):
            continue
        item = _annotated_source_record(source, source_id, annotation)
        if pinned_only and not (item.get("pinned") or _normalize_text(item.get("tier")).lower() == "canon"):
            continue
        items.append(item)

    items.sort(key=_item_sort_key, reverse=True)
    return items


def _filter_items(items: list[dict[str, Any]], request: MemorySearchRequest) -> list[dict[str, Any]]:
    tag_filter = set(_normalize_tags(request.tags))
    strategy_id = _normalize_text(request.strategy_id).upper() or None
    agent_id = _normalize_text(request.agent_id) or None
    from_dt, to_dt = _resolve_time_range(request.time_range)

    filtered: list[dict[str, Any]] = []
    for item in items:
        if not request.include_hidden and bool(item.get("hidden")):
            continue
        if strategy_id and _normalize_text(item.get("strategy_id")).upper() != strategy_id:
            continue
        if agent_id and _normalize_text(item.get("agent_id")) != agent_id:
            continue
        if tag_filter and not (tag_filter & set(_normalize_tags(item.get("tags")))):
            continue
        if from_dt or to_dt:
            item_dt = _to_datetime(item.get("updated_at") or item.get("created_at"))
            if item_dt is None:
                continue
            if from_dt and item_dt < from_dt:
                continue
            if to_dt and item_dt > to_dt:
                continue
        filtered.append(item)

    return filtered


def _item_sort_key(item: dict[str, Any]) -> tuple[int, int, float, float]:
    pinned_rank = 1 if item.get("pinned") else 0
    tier_rank = TIER_RANKS.get(_normalize_text(item.get("tier")).lower(), 0)
    score = float(item.get("score") or 0.0)
    updated = _datetime_sort_key(item.get("updated_at") or item.get("created_at"))
    return pinned_rank, tier_rank, score, updated


def _paginate_items(items: list[dict[str, Any]], page: int, limit: int, cursor: str | None) -> tuple[list[dict[str, Any]], int, str | None]:
    normalized_limit = max(1, min(int(limit or DEFAULT_PAGE_LIMIT), MAX_PAGE_LIMIT))
    offset = 0
    if _normalize_text(cursor):
        try:
            offset = max(0, int(str(cursor).split(":")[-1]))
        except Exception:
            offset = 0
    else:
        offset = max(0, (int(page or 1) - 1) * normalized_limit)

    paged = items[offset: offset + normalized_limit]
    next_offset = offset + normalized_limit
    next_cursor = f"offset:{next_offset}" if next_offset < len(items) else None
    return paged, len(items), next_cursor


def _query_text_match_score(query: str, *fields: str) -> float:
    normalized_query = _normalize_text(query).lower()
    if not normalized_query:
        return 0.0
    tokens = [token for token in re.split(r"\s+", normalized_query) if token]
    haystacks = [str(field or "").lower() for field in fields]
    combined = "\n".join(haystacks)
    if not combined:
        return 0.0
    score = 0.0
    if normalized_query in combined:
        score += 1.5
    for token in tokens:
        score += combined.count(token) * 0.2
    return score


def _score_workspace_items(items: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    if not _normalize_text(query):
        return items
    scored: list[dict[str, Any]] = []
    for item in items:
        score = _query_text_match_score(query, item.get("title", ""), item.get("excerpt", ""), item.get("content_preview", ""))
        if score <= 0:
            continue
        updated = dict(item)
        updated["score"] = score
        scored.append(updated)
    return scored


def _combine_items(
    *,
    request: MemorySearchRequest,
    workspace_items: list[dict[str, Any]],
    chroma_items: list[dict[str, Any]],
    narrative_items: list[dict[str, Any]],
    annotations: dict[tuple[str, str], dict[str, Any]],
) -> list[dict[str, Any]]:
    selected_sources = set(_normalize_source_selection(request.sources))
    items: list[dict[str, Any]] = []

    for item in workspace_items + chroma_items + narrative_items:
        if item.get("source") not in selected_sources:
            continue
        annotation = annotations.get((str(item.get("source")), str(item.get("source_id"))))
        items.append(_apply_annotation(item, annotation))

    return _filter_items(items, request)


def _memory_metrics(items: list[dict[str, Any]], annotations: dict[tuple[str, str], dict[str, Any]]) -> dict[str, Any]:
    source_counts = {
        source: len([item for item in items if item.get("source") == source and not item.get("hidden")])
        for source in SUPPORTED_SOURCES
    }
    hidden_count = len([item for item in items if item.get("hidden")])
    canon_count = len([item for item in items if item.get("pinned") or _normalize_text(item.get("tier")).lower() == "canon"])
    annotated_count = len([annotation for annotation in annotations.values() if _annotation_is_meaningful(annotation)])
    return {
        "visible_count": len([item for item in items if not item.get("hidden")]),
        "hidden_count": hidden_count,
        "canon_count": canon_count,
        "annotated_count": annotated_count,
        "source_counts": source_counts,
    }


def _timeline_entries(
    items: list[dict[str, Any]],
    annotations: dict[tuple[str, str], dict[str, Any]],
    limit: int = DEFAULT_TIMELINE_LIMIT,
) -> list[dict[str, Any]]:
    with get_db() as conn:
        _ensure_memory_tables(conn)
        rows = conn.execute(
            """
            SELECT id, source, source_id, action, payload_json, actor, created_at
            FROM memory_events
            WHERE action <> 'snapshot'
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (max(limit * 2, 30),),
        ).fetchall()

    item_lookup = {(str(item.get("source")), str(item.get("source_id"))): item for item in items}
    timeline: list[dict[str, Any]] = []
    for row in rows:
        payload = _safe_json_loads(row["payload_json"], {})
        if not isinstance(payload, dict):
            payload = {}
        source = _normalize_text(row["source"])
        source_id = _normalize_text(row["source_id"])
        item = item_lookup.get((source, source_id))
        if item is None:
            annotation = annotations.get((source, source_id))
            if annotation is not None:
                item = _annotated_source_record(source, source_id, annotation)
        timeline.append(
            {
                "kind": "event",
                "timestamp": _to_iso(row["created_at"]) or row["created_at"],
                "action": _normalize_text(row["action"]) or "event",
                "actor": _normalize_text(row["actor"]) or "operator",
                "source": source,
                "source_id": source_id,
                "summary": _normalize_text(payload.get("summary")) or _normalize_text(row["action"]).replace("_", " ").title(),
                "item": item,
            }
        )

    for item in items[: max(limit, 8)]:
        timeline.append(
            {
                "kind": "source",
                "timestamp": item.get("updated_at") or item.get("created_at"),
                "action": "observed",
                "actor": "system",
                "source": item.get("source"),
                "source_id": item.get("source_id"),
                "summary": item.get("title"),
                "item": item,
            }
        )

    timeline.sort(key=lambda entry: _datetime_sort_key(entry.get("timestamp")), reverse=True)
    return timeline[:limit]


def _canon_items(items: list[dict[str, Any]], limit: int = DEFAULT_CANON_LIMIT) -> list[dict[str, Any]]:
    canon = [
        item
        for item in items
        if not item.get("hidden")
        and (item.get("pinned") or _normalize_text(item.get("tier")).lower() == "canon")
    ]
    canon.sort(key=_item_sort_key, reverse=True)
    return canon[:limit]


def _curation_candidates(items: list[dict[str, Any]], limit: int = 8) -> list[dict[str, Any]]:
    candidates = [
        item
        for item in items
        if not item.get("hidden")
        and not item.get("pinned")
        and not _normalize_text(item.get("tier"))
        and not _normalize_text(item.get("note"))
    ]
    candidates.sort(key=_item_sort_key, reverse=True)
    return candidates[:limit]


def _is_protected_memory_item(item: dict[str, Any], annotation: dict[str, Any] | None) -> bool:
    if item.get("pinned") or _normalize_text(item.get("tier")).lower() == "canon":
        return True
    if annotation is None:
        return False
    if annotation.get("pinned") or _normalize_text(annotation.get("tier")).lower() == "canon":
        return True
    return _annotation_is_meaningful(annotation)


def _maintenance_candidate_record(
    item: dict[str, Any],
    *,
    reason: str,
    action: str,
) -> dict[str, Any]:
    return {
        "source": item.get("source"),
        "source_id": item.get("source_id"),
        "title": item.get("title"),
        "agent_id": item.get("agent_id"),
        "updated_at": item.get("updated_at") or item.get("created_at"),
        "tags": item.get("tags") or [],
        "reason": reason,
        "action": action,
    }


def _memory_maintenance_preview(body: MemoryMaintenanceRequest) -> dict[str, Any]:
    annotations = _load_annotations_map()
    workspace_items = [
        _apply_annotation(item, annotations.get((str(item.get("source")), str(item.get("source_id")))))
        for item in _workspace_items()
    ]
    cutoff = datetime.now(timezone.utc) - timedelta(days=body.older_than_days)

    daily_file_items: list[dict[str, Any]] = []
    daily_signal_sections: list[dict[str, Any]] = []
    protected_daily_items = 0
    agent_counts: dict[str, int] = {}
    file_paths: set[str] = set()

    for item in workspace_items:
        source_id = _normalize_text(item.get("source_id"))
        if item.get("source") != WORKSPACE_SOURCE or not _daily_agent_log_match(_daily_agent_log_source_path(source_id)):
            continue
        item_date = _daily_agent_log_date(source_id)
        if item_date is None or item_date >= cutoff:
            continue
        annotation = annotations.get((WORKSPACE_SOURCE, source_id))
        if _is_protected_memory_item(item, annotation):
            protected_daily_items += 1
            continue

        agent_id = _normalize_text(item.get("agent_id")) or "unknown"
        agent_counts[agent_id] = agent_counts.get(agent_id, 0) + 1
        file_paths.add(_daily_agent_log_source_path(source_id))

        if "#" in source_id:
            daily_signal_sections.append(
                _maintenance_candidate_record(
                    item,
                    reason=f"High-signal section from daily agent log older than {body.older_than_days} days.",
                    action="keep_visible_for_now",
                )
            )
        else:
            daily_file_items.append(
                _maintenance_candidate_record(
                    item,
                    reason=f"Raw daily agent log older than {body.older_than_days} days.",
                    action="compact_and_hide" if body.compact_daily_logs and body.hide_old_daily_logs else "review",
                )
            )

    daily_file_items.sort(key=lambda item: _datetime_sort_key(item.get("updated_at")), reverse=True)
    daily_signal_sections.sort(key=lambda item: _datetime_sort_key(item.get("updated_at")), reverse=True)

    total_daily_file_candidates = len(daily_file_items)
    total_signal_sections = len(daily_signal_sections)
    limited_daily_file_items = daily_file_items[: body.limit]
    limited_signal_sections = daily_signal_sections[: body.limit]
    estimated_hidden = total_daily_file_candidates if body.hide_old_daily_logs else 0

    return {
        "dry_run": bool(body.dry_run),
        "older_than_days": body.older_than_days,
        "cutoff": cutoff.isoformat(),
        "summary": {
            "daily_log_files_to_compact": len(file_paths) if body.compact_daily_logs else 0,
            "daily_file_items_to_hide": estimated_hidden,
            "daily_signal_sections_seen": total_signal_sections,
            "protected_daily_items": protected_daily_items,
            "estimated_visible_reduction": estimated_hidden,
            "archive_narratives": bool(body.archive_narratives),
        },
        "agent_counts": dict(sorted(agent_counts.items(), key=lambda entry: (-entry[1], entry[0]))),
        "candidates": {
            "daily_file_items": limited_daily_file_items,
            "daily_signal_sections": limited_signal_sections,
        },
        "truncated": {
            "daily_file_items": max(0, total_daily_file_candidates - len(limited_daily_file_items)),
            "daily_signal_sections": max(0, total_signal_sections - len(limited_signal_sections)),
        },
        "next_actions": [
            "Review daily_file_items before enabling non-dry-run maintenance.",
            "Generate deterministic daily summaries before hiding raw daily logs.",
            "Keep high-signal sections visible until canon and ranking workflows are in place.",
        ],
    }


def _write_text_if_changed(path: Path, content: str) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        existing = path.read_text(encoding="utf-8")
    except OSError:
        existing = None
    if existing == content:
        return False
    path.write_text(content, encoding="utf-8")
    return True


def _hide_compacted_daily_log(source_id: str, *, summary_relative_path: str, actor: str) -> None:
    existing = _get_annotation(WORKSPACE_SOURCE, source_id) or {}
    tags = _merge_tags(_normalize_tags(existing.get("tags")), ["daily", "compacted"])
    note = _normalize_text(existing.get("note")) or f"Compacted to {summary_relative_path}."
    now = _now_iso()

    with get_db() as conn:
        _ensure_memory_tables(conn)
        conn.execute(
            """
            INSERT INTO memory_annotations (
                source, source_id, source_kind, title_override, tags_json, note, tier, pinned, hidden, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source, source_id) DO UPDATE SET
                source_kind = excluded.source_kind,
                title_override = excluded.title_override,
                tags_json = excluded.tags_json,
                note = excluded.note,
                tier = excluded.tier,
                pinned = excluded.pinned,
                hidden = excluded.hidden,
                updated_at = excluded.updated_at
            """,
            (
                WORKSPACE_SOURCE,
                source_id,
                existing.get("source_kind") or "workspace_daily_log",
                existing.get("title_override"),
                _safe_json_dumps(tags),
                note,
                existing.get("tier"),
                1 if existing.get("pinned") else 0,
                1,
                now,
            ),
        )

    _record_memory_event(
        source=WORKSPACE_SOURCE,
        source_id=source_id,
        action="maintenance_compact",
        actor=actor,
        payload={
            "summary": "Daily log compacted and hidden from default memory views",
            "summary_relative_path": summary_relative_path,
        },
    )


def _run_memory_maintenance(body: MemoryMaintenanceRequest) -> dict[str, Any]:
    preview = _memory_maintenance_preview(body)
    actor = "memory-maintenance"
    written_summaries: list[dict[str, Any]] = []
    hidden_items: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for candidate in preview["candidates"]["daily_file_items"]:
        source_id = _normalize_text(candidate.get("source_id"))
        source_path = _daily_agent_log_source_path(source_id)
        source_file = _resolve_workspace_file(source_path)
        summary_relative_path = _daily_agent_summary_relative_path(source_path)
        summary_path = _daily_agent_summary_path(source_path)
        if source_file is None or summary_path is None or summary_relative_path is None:
            skipped.append({"source_id": source_id, "reason": "source or summary path unavailable"})
            continue

        try:
            content = source_file.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            skipped.append({"source_id": source_id, "reason": f"read failed: {exc}"})
            continue

        if body.compact_daily_logs:
            summary_content = _daily_agent_log_summary(source_path, content)
            changed = _write_text_if_changed(summary_path, summary_content)
            written_summaries.append(
                {
                    "source_id": source_id,
                    "summary_relative_path": summary_relative_path,
                    "changed": changed,
                }
            )

        if body.hide_old_daily_logs:
            _hide_compacted_daily_log(
                source_id,
                summary_relative_path=summary_relative_path or "",
                actor=actor,
            )
            hidden_items.append({"source_id": source_id, "summary_relative_path": summary_relative_path})

    preview["dry_run"] = False
    preview["applied"] = {
        "summaries_written": len(written_summaries),
        "summaries_changed": len([entry for entry in written_summaries if entry.get("changed")]),
        "daily_file_items_hidden": len(hidden_items),
        "skipped": len(skipped),
    }
    preview["written_summaries"] = written_summaries[: body.limit]
    preview["hidden_items"] = hidden_items[: body.limit]
    preview["skipped"] = skipped[: body.limit]
    preview["next_actions"] = [
        "Review generated summaries in agents/*/memory/summaries/.",
        "Use include_hidden=true if raw compacted daily logs need inspection.",
        "Promote durable lessons from summaries or high-signal sections into canon.",
    ]
    return preview


def _source_health() -> list[dict[str, Any]]:
    return [_workspace_health(), _chroma_health(), _narratives_health()]


def _update_narratives_health(
    source_health: list[dict[str, Any]],
    items: list[dict[str, Any]],
    *,
    mode: str,
) -> None:
    narratives_health = next(
        (entry for entry in source_health if _normalize_text(entry.get("source")) == NARRATIVES_SOURCE),
        None,
    )
    if not isinstance(narratives_health, dict) or not narratives_health.get("configured"):
        return

    item_count = len([item for item in items if isinstance(item, dict)])
    timestamps = [
        _normalize_text(item.get("updated_at")) or _normalize_text(item.get("created_at"))
        for item in items
        if isinstance(item, dict)
    ]

    narratives_health["count"] = item_count
    narratives_health["latest_updated_at"] = max(timestamps) if timestamps else None

    if mode == "search":
        narratives_health["summary"] = (
            f"{item_count} live narrative match{'es' if item_count != 1 else ''} for this search"
            if item_count
            else "No narrative matches for this search"
        )
        return

    if mode == "browse_live":
        narratives_health["summary"] = (
            f"{item_count} live narrative item{'s' if item_count != 1 else ''} loaded in browse mode"
            if item_count
            else "No live narrative items returned for browse mode"
        )
        return

    narratives_health["summary"] = (
        f"{item_count} curated narrative item{'s' if item_count != 1 else ''} cached for browse mode"
        if item_count
        else "No cached narrative items yet. Live browse falls back to search if needed."
    )


def get_memory_nav_indicator() -> dict[str, Any]:
    try:
        annotations = _load_annotations_map()
    except Exception:
        return _empty_nav_indicator()

    backlog = len(
        [
            annotation
            for annotation in annotations.values()
            if not annotation.get("hidden")
            and not annotation.get("pinned")
            and not _normalize_text(annotation.get("tier"))
            and not _normalize_text(annotation.get("note"))
        ]
    )
    if backlog > 0:
        return _build_nav_indicator(
            "count",
            "info",
            str(backlog),
            f"{backlog} memory item{'s' if backlog != 1 else ''} need curation",
            f"memory:backlog:{backlog}",
            count=backlog,
        )

    canon = len(
        [
            annotation
            for annotation in annotations.values()
            if annotation.get("pinned") or _normalize_text(annotation.get("tier")).lower() == "canon"
        ]
    )
    if canon > 0:
        return _build_nav_indicator(
            "status",
            "success",
            "CANON",
            f"{canon} curated memory item{'s' if canon != 1 else ''} pinned",
            f"memory:canon:{canon}",
            count=canon,
        )

    return _empty_nav_indicator()


def get_memory_overview(limit: int = DEFAULT_PAGE_LIMIT) -> dict[str, Any]:
    annotations = _load_annotations_map()
    workspace_items = _workspace_items()
    chroma_items, chroma_stats = _browse_chroma_items(list(CHROMA_COLLECTIONS), max(limit, 12))
    live_narratives = _browse_narrative_items_sync(max(limit, 12))
    cached_narratives = _cached_narrative_items(annotations)
    narrative_items = live_narratives or cached_narratives

    items = [
        _apply_annotation(item, annotations.get((str(item.get("source")), str(item.get("source_id")))))
        for item in (workspace_items + chroma_items + narrative_items)
    ]
    items.sort(key=_item_sort_key, reverse=True)

    source_health = _source_health()
    if source_health[1]["count"] == 0 and chroma_stats.get("ok"):
        source_health[1]["summary"] = "No Chroma records yet"
    _update_narratives_health(
        source_health,
        narrative_items,
        mode="browse_live" if live_narratives else "browse_cached",
    )
    # Drop ChromaDB-backed source rows when the vector layer is disabled on this
    # host — they are dead capability, not a configured-but-empty store.
    source_health = [s for s in source_health if not s.get("vector_layer_disabled")]

    return {
        "metrics": _memory_metrics(items, annotations),
        "source_health": source_health,
        "canon_items": _canon_items(items, DEFAULT_CANON_LIMIT),
        "timeline": _timeline_entries(items, annotations, DEFAULT_TIMELINE_LIMIT),
        "curation_candidates": _curation_candidates(items, 8),
        "recent_items": items[: max(limit, 12)],
    }


async def search_memory_records(body: MemorySearchRequest) -> dict[str, Any]:
    annotations = _load_annotations_map()
    query = _normalize_text(body.query)
    collections = _normalize_collection_selection(body.collections)
    source_health = _source_health()

    workspace_items = _workspace_items()
    if query:
        workspace_items = _score_workspace_items(workspace_items, query)
    else:
        workspace_items = workspace_items[: max(body.limit * 2, 24)]

    if query:
        chroma_items, chroma_stats = _search_chroma_items(query, collections, max(body.limit, 12))
        narrative_items = await _search_narrative_items(query, max(body.limit, 8))
        _update_narratives_health(source_health, narrative_items, mode="search")
    else:
        chroma_items, chroma_stats = _browse_chroma_items(collections, max(body.limit, 12))
        live_narratives = await _browse_narrative_items(max(body.limit, 12))
        cached_narratives = _cached_narrative_items(annotations)
        narrative_items = live_narratives or cached_narratives
        _update_narratives_health(
            source_health,
            narrative_items,
            mode="browse_live" if live_narratives else "browse_cached",
        )

    items = _combine_items(
        request=body,
        workspace_items=workspace_items,
        chroma_items=chroma_items,
        narrative_items=narrative_items,
        annotations=annotations,
    )
    items.sort(key=_item_sort_key, reverse=True)
    paged, total, next_cursor = _paginate_items(items, body.page, body.limit, body.cursor)

    # Drop dead ChromaDB-backed source rows when the vector layer is disabled.
    source_health = [s for s in source_health if not s.get("vector_layer_disabled")]

    payload = {
        "query": query,
        "page": body.page,
        "limit": body.limit,
        "total": total,
        "next_cursor": next_cursor,
        "results": paged,
        "source_health": source_health,
        "available_collections": [
            {
                "name": collection,
                "label": CHROMA_COLLECTION_LABELS.get(collection, collection),
                "count": int(((chroma_stats.get("collections") or {}).get(collection) or {}).get("count") or 0),
            }
            for collection in CHROMA_COLLECTIONS
        ],
    }

    if not query:
        payload.update(
            {
                "metrics": _memory_metrics(items, annotations),
                "source_health": source_health,
                "canon_items": _canon_items(items, DEFAULT_CANON_LIMIT),
                "timeline": _timeline_entries(items, annotations, DEFAULT_TIMELINE_LIMIT),
                "curation_candidates": _curation_candidates(items, 8),
                "recent_items": items[: max(body.limit, 12)],
            }
        )

    return payload


def get_memory_maintenance_preview(
    older_than_days: int = DEFAULT_MAINTENANCE_OLDER_THAN_DAYS,
    limit: int = MAX_MAINTENANCE_CANDIDATES,
) -> dict[str, Any]:
    return _memory_maintenance_preview(
        MemoryMaintenanceRequest(
            dry_run=True,
            older_than_days=older_than_days,
            limit=limit,
        )
    )


def run_memory_maintenance(body: MemoryMaintenanceRequest) -> dict[str, Any]:
    if body.dry_run:
        return _memory_maintenance_preview(body)
    if not body.compact_daily_logs and not body.hide_old_daily_logs and not body.archive_narratives:
        raise HTTPException(status_code=400, detail="no maintenance actions selected")
    return _run_memory_maintenance(body)


def _related_items(item: dict[str, Any], annotations: dict[tuple[str, str], dict[str, Any]], limit: int = 6) -> list[dict[str, Any]]:
    workspace_items = _workspace_items()[:50]
    chroma_items, _ = _browse_chroma_items(list(CHROMA_COLLECTIONS), 20)
    cached_narratives = _cached_narrative_items(annotations)
    combined = [
        _apply_annotation(candidate, annotations.get((str(candidate.get("source")), str(candidate.get("source_id")))))
        for candidate in (workspace_items + chroma_items + cached_narratives)
    ]

    base_source = _normalize_text(item.get("source"))
    base_source_id = _normalize_text(item.get("source_id"))
    base_strategy = _normalize_text(item.get("strategy_id")).upper()
    base_agent = _normalize_text(item.get("agent_id"))
    base_tags = set(_normalize_tags(item.get("tags")))

    related: list[dict[str, Any]] = []
    for candidate in combined:
        if _normalize_text(candidate.get("source")) == base_source and _normalize_text(candidate.get("source_id")) == base_source_id:
            continue
        shared_tags = base_tags & set(_normalize_tags(candidate.get("tags")))
        same_strategy = base_strategy and _normalize_text(candidate.get("strategy_id")).upper() == base_strategy
        same_agent = base_agent and _normalize_text(candidate.get("agent_id")) == base_agent
        if same_strategy or same_agent or shared_tags:
            related.append(candidate)

    related.sort(key=_item_sort_key, reverse=True)
    return related[:limit]


def get_memory_item(source: str, source_id: str) -> dict[str, Any]:
    normalized_source = _normalize_text(source).lower()
    normalized_source_id = _normalize_text(source_id)
    if normalized_source not in SUPPORTED_SOURCES or not normalized_source_id:
        raise HTTPException(status_code=404, detail="memory item not found")

    annotation = _get_annotation(normalized_source, normalized_source_id)
    item: dict[str, Any] | None = None

    if normalized_source == WORKSPACE_SOURCE:
        workspace_lookup = {entry["source_id"]: entry for entry in _workspace_items()}
        item = workspace_lookup.get(normalized_source_id)
    elif normalized_source == CHROMA_SOURCE:
        item = _load_chroma_item(normalized_source_id)
    elif normalized_source == NARRATIVES_SOURCE:
        if annotation:
            item = _annotated_source_record(normalized_source, normalized_source_id, annotation)
        else:
            snapshot = _latest_item_snapshot(normalized_source, normalized_source_id)
            if snapshot:
                item = snapshot

    if item is None and annotation is not None:
        item = _annotated_source_record(normalized_source, normalized_source_id, annotation)
    if item is None:
        raise HTTPException(status_code=404, detail="memory item not found")

    applied_item = _apply_annotation(item, annotation)
    annotations = _load_annotations_map()
    return {
        "item": applied_item,
        "annotation": annotation,
        "events": _load_memory_events(normalized_source, normalized_source_id),
        "related_items": _related_items(applied_item, annotations),
    }


def update_memory_annotation(source: str, source_id: str, body: MemoryAnnotationBody) -> dict[str, Any]:
    normalized_source = _normalize_text(source).lower()
    normalized_source_id = _normalize_text(source_id)
    if normalized_source not in SUPPORTED_SOURCES or not normalized_source_id:
        raise HTTPException(status_code=404, detail="memory item not found")

    existing = _get_annotation(normalized_source, normalized_source_id) or {
        "source": normalized_source,
        "source_id": normalized_source_id,
        "source_kind": None,
        "title_override": None,
        "tags": [],
        "note": None,
        "tier": None,
        "pinned": False,
        "hidden": False,
        "updated_at": None,
    }

    merged = dict(existing)
    if body.source_kind is not None:
        merged["source_kind"] = _normalize_text(body.source_kind) or None
    if body.title_override is not None:
        merged["title_override"] = _normalize_text(body.title_override) or None
    if body.tags is not None:
        merged["tags"] = _normalize_tags(body.tags)
    if body.note is not None:
        merged["note"] = _normalize_text(body.note) or None
    if body.tier is not None:
        merged["tier"] = _normalize_text(body.tier).lower() or None
    if body.pinned is not None:
        merged["pinned"] = bool(body.pinned)
    if body.hidden is not None:
        merged["hidden"] = bool(body.hidden)
    merged["updated_at"] = _now_iso()

    with get_db() as conn:
        _ensure_memory_tables(conn)
        conn.execute(
            """
            INSERT INTO memory_annotations (
                source, source_id, source_kind, title_override, tags_json, note, tier, pinned, hidden, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source, source_id) DO UPDATE SET
                source_kind = excluded.source_kind,
                title_override = excluded.title_override,
                tags_json = excluded.tags_json,
                note = excluded.note,
                tier = excluded.tier,
                pinned = excluded.pinned,
                hidden = excluded.hidden,
                updated_at = excluded.updated_at
            """,
            (
                normalized_source,
                normalized_source_id,
                merged.get("source_kind"),
                merged.get("title_override"),
                _safe_json_dumps(_normalize_tags(merged.get("tags"))),
                merged.get("note"),
                merged.get("tier"),
                1 if merged.get("pinned") else 0,
                1 if merged.get("hidden") else 0,
                merged.get("updated_at"),
            ),
        )

    _record_memory_event(
        source=normalized_source,
        source_id=normalized_source_id,
        action="annotate",
        actor=body.actor,
        payload={
            "summary": "Annotation updated",
            "annotation": merged,
            "snapshot": _clip_item_snapshot(body.item_snapshot),
        },
    )

    return get_memory_item(normalized_source, normalized_source_id)


async def apply_memory_action(source: str, source_id: str, body: MemoryActionBody) -> dict[str, Any]:
    normalized_source = _normalize_text(source).lower()
    normalized_source_id = _normalize_text(source_id)
    action = _normalize_text(body.action).lower()

    if normalized_source not in SUPPORTED_SOURCES or not normalized_source_id:
        raise HTTPException(status_code=404, detail="memory item not found")
    if action not in {"hide", "unhide"}:
        raise HTTPException(status_code=400, detail=f"unsupported memory action: {action}")

    snapshot = _clip_item_snapshot(body.item_snapshot)
    annotation = _get_annotation(normalized_source, normalized_source_id) or {
        "source": normalized_source,
        "source_id": normalized_source_id,
        "source_kind": None,
        "title_override": None,
        "tags": [],
        "note": None,
        "tier": None,
        "pinned": False,
        "hidden": False,
        "updated_at": None,
    }

    response_payload: dict[str, Any] = {"ok": True, "action": action}
    if action == "hide":
        annotation["hidden"] = True
        annotation["updated_at"] = _now_iso()
    elif action == "unhide":
        annotation["hidden"] = False
        annotation["updated_at"] = _now_iso()

    with get_db() as conn:
        _ensure_memory_tables(conn)
        conn.execute(
            """
            INSERT INTO memory_annotations (
                source, source_id, source_kind, title_override, tags_json, note, tier, pinned, hidden, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source, source_id) DO UPDATE SET
                source_kind = excluded.source_kind,
                title_override = excluded.title_override,
                tags_json = excluded.tags_json,
                note = excluded.note,
                tier = excluded.tier,
                pinned = excluded.pinned,
                hidden = excluded.hidden,
                updated_at = excluded.updated_at
            """,
            (
                normalized_source,
                normalized_source_id,
                annotation.get("source_kind"),
                annotation.get("title_override"),
                _safe_json_dumps(_normalize_tags(annotation.get("tags"))),
                annotation.get("note"),
                annotation.get("tier"),
                1 if annotation.get("pinned") else 0,
                1 if annotation.get("hidden") else 0,
                annotation.get("updated_at") or _now_iso(),
            ),
        )

    _record_memory_event(
        source=normalized_source,
        source_id=normalized_source_id,
        action=action,
        actor=body.actor,
        payload={
            "summary": {
                "hide": "Memory hidden from default views",
                "unhide": "Memory restored to default views",
            }.get(action, action),
            "snapshot": snapshot,
        },
    )

    detail = get_memory_item(normalized_source, normalized_source_id)
    response_payload["item"] = detail.get("item")
    response_payload["events"] = detail.get("events")
    return response_payload


__all__ = [
    "MemoryActionBody",
    "MemoryAnnotationBody",
    "MemoryMaintenanceRequest",
    "MemorySearchRequest",
    "MemoryTimeRange",
    "apply_memory_action",
    "get_memory_item",
    "get_memory_maintenance_preview",
    "get_memory_nav_indicator",
    "get_memory_overview",
    "run_memory_maintenance",
    "search_memory_records",
    "update_memory_annotation",
]
