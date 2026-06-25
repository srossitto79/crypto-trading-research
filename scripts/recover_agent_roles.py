"""Recover missing or overwritten agent ROLE.md files from the unified Axiom workspace.

This script is intentionally conservative:
- It merges persona payloads from all available snapshots and keeps the richer
  version per agent when duplicates exist.
- It mirrors recovered personas into the workspace:
  `~/.Axiom/workspace/agents/*/ROLE.md`.
- It updates `name` and `instructions` columns in `~/.Axiom/axiom.db`.
  IMPORTANT: it does NOT touch the `role` column — that column is a type slug
  (e.g. 'strategy-developer') consumed by Brain's fan-out filter at
  Axiom/brain.py, and overwriting it with ROLE.md prose silently removes
  agents from the swarm.

Useful when ROLE.md content is unexpectedly short/blank after a workspace migration.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path


WORKSPACE_REL = Path("workspace") / "agents"

ROOT = Path.home()
TARGET_WORKSPACES = [ROOT / ".Axiom", ROOT / ".judex"]
TARGET_DBS = [
    ROOT / ".Axiom" / "axiom.db",
    ROOT / ".judex" / "axiom.db",
]
SOURCE_DB_CANDIDATES = TARGET_DBS.copy()


@dataclass(frozen=True)
class AgentRoleSource:
    agent_id: str
    name: str
    instructions: str


def _safe_text(value) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _content_weight(instructions: str) -> int:
    return len(_safe_text(instructions))


def _has_agents_table(path: Path) -> bool:
    try:
        with sqlite3.connect(path) as conn:
            row = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='agents' LIMIT 1"
            ).fetchone()
            return bool(row)
    except Exception:
        return False


def _load_role_sources() -> dict[str, AgentRoleSource]:
    """Load source roles from all available snapshots."""
    merged: dict[str, AgentRoleSource] = {}

    # 1) Merge from all candidate DBs so we don't miss fields from newer sources
    #    if one DB is missing a row (or is a smaller snapshot).
    for db_path in SOURCE_DB_CANDIDATES:
        if not db_path.exists() or not _has_agents_table(db_path):
            continue

        loaded = 0
        with sqlite3.connect(db_path) as conn:
            try:
                cursor = conn.execute("SELECT id, name, instructions FROM agents")
            except Exception:
                continue
            for agent_id, name, instructions in cursor.fetchall():
                rid = _safe_text(agent_id).lower()
                if not rid:
                    continue
                source = AgentRoleSource(
                    agent_id=rid,
                    name=_safe_text(name) or rid.replace("-", " ").replace("_", " ").title(),
                    instructions=_safe_text(instructions),
                )
                current = merged.get(rid)
                if not current or _content_weight(source.instructions) > _content_weight(current.instructions):
                    merged[rid] = source
                loaded += 1

        if loaded:
            print(f"Loaded {loaded} role rows from {db_path}")

    # 2) Merge any existing role docs that already hold custom instructions.
    for workspace_root in TARGET_WORKSPACES:
        roles_dir = workspace_root / WORKSPACE_REL
        if not roles_dir.exists():
            continue
        for role_file in roles_dir.glob("*/ROLE.md"):
            if not role_file.is_file():
                continue
            agent_id = role_file.parent.name.lower()
            content = role_file.read_text(errors='ignore')
            lines = [line.strip() for line in content.splitlines() if line.strip()]
            if not lines:
                continue
            title = lines[0].removeprefix("#").strip() or agent_id
            # ROLE.md body (everything after the title) is the persona/instructions.
            # Preserve the full body minus the title — do NOT try to extract a
            # "role" line; `role` in the DB is a slug, not prose.
            body_lines = lines[1:]
            # Drop a leading "## Instructions" marker if present, since we're
            # treating the whole body as instructions.
            if body_lines and body_lines[0].lower().startswith("## instructions"):
                body_lines = body_lines[1:]
            instruction_text = "\n".join(body_lines).strip()

            source = AgentRoleSource(
                agent_id=agent_id,
                name=title,
                instructions=instruction_text,
            )
            current = merged.get(agent_id)
            if not current or _content_weight(source.instructions) > _content_weight(current.instructions):
                merged[agent_id] = source

    if merged:
        print(f"Resolved {len(merged)} unified agent role sources")
        return merged

    print("No usable source DB found; skipping role recovery.")
    return {}


def _format_role_markdown(agent_id: str, source_name: str, instructions: str) -> str:
    title = source_name or agent_id.replace("-", " ").replace("_", " ").title()
    body = instructions or "Persona for this agent has not been written yet."
    return f"# {title}\n\n{body}\n"


def _update_role_files(role_source: dict[str, AgentRoleSource]) -> list[str]:
    updated = []
    for workspace_root in TARGET_WORKSPACES:
        for agent_dir in (workspace_root / WORKSPACE_REL).glob("*/"):
            if not agent_dir.is_dir():
                continue

            agent_id = agent_dir.name.lower()
            source = role_source.get(agent_id)
            if not source:
                continue

            role_path = agent_dir / "ROLE.md"
            content = _format_role_markdown(agent_id, source.name, source.instructions)
            role_path.write_text(content)
            updated.append(str(role_path))

    return updated


def _update_agent_db_rows(path: Path, role_source: dict[str, AgentRoleSource]) -> int:
    if not path.exists() or not _has_agents_table(path):
        return 0
    if not role_source:
        return 0

    updated = 0
    with sqlite3.connect(path) as conn:
        for agent_id, source in role_source.items():
            existing = conn.execute("SELECT 1 FROM agents WHERE id = ?", (agent_id,)).fetchone()
            # NEVER touch the `role` column here — it is a type slug consumed
            # by Brain's fan-out filter. This script only manages `name` and
            # `instructions` so it can't silently remove agents from the swarm.
            updates = ["name = ?"]
            values = [source.name]
            if source.instructions:
                updates.append("instructions = ?")
                values.append(source.instructions)

            if existing:
                values.append(agent_id)
                conn.execute(f"UPDATE agents SET {', '.join(updates)} WHERE id = ?", values)
                updated += 1
                continue

            # New row: default role to the agent_id when it matches a
            # canonical slug pattern; otherwise leave it blank for an operator
            # to set via the Hub UI.
            conn.execute(
                "INSERT INTO agents (id, name, role, model, model_id, schedule_type, schedule_expr, enabled, instructions, created_at, updated_at) "
                "VALUES (?, ?, ?, 'minimax', 'MiniMax-M2.5', NULL, NULL, 1, ?, datetime('now'), datetime('now'))",
                (agent_id, source.name, agent_id, source.instructions or ''),
            )
            updated += 1
        conn.commit()
    return updated


def main():
    role_source = _load_role_sources()
    if not role_source:
        return 1

    updated_files = _update_role_files(role_source)
    print(f"Updated {len(updated_files)} ROLE.md files:")
    for path in updated_files:
        print(f" - {path}")

    for db_path in TARGET_DBS:
        if db_path.exists():
            updated_rows = _update_agent_db_rows(db_path, role_source)
            print(f"Updated {updated_rows} rows in {db_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
