"""Per-agent identity files (SOUL.md / AGENTS.md / ROLE.md).

Each sub-agent gets its OWN copy of SOUL.md and AGENTS.md (seeded from the
shipped templates, lightly personalized) plus a bespoke ROLE.md. A single
GLOBAL IDENTITY.md is shared by all agents.

These tests do real workspace file I/O, so they pin the module-level
WORKSPACE_DIR bindings (captured at import time) to the per-test AXIOM_HOME.
The conftest `_isolate_AXIOM_home` fixture only patches `cfg.WORKSPACE_DIR`,
which does not reach the `from axiom.config import WORKSPACE_DIR` aliases that
`workspace.py` / `manager.py` bound at import time.
"""

from contextlib import contextmanager


@contextmanager
def _pin_workspace_dir(home):
    """Point every module-level WORKSPACE_DIR / LEGACY_WORKSPACE_DIR alias at
    the per-test home so read/write_workspace touch the temp dir, not ~/.Axiom.
    """
    from unittest.mock import patch

    ws_dir = home / "workspace"
    ws_dir.mkdir(parents=True, exist_ok=True)
    (ws_dir / "agents").mkdir(exist_ok=True)
    (ws_dir / "memory").mkdir(exist_ok=True)

    import axiom.config as cfg
    import axiom.workspace as ws_mod
    import axiom.agents.manager as mgr_mod

    legacy = ws_dir  # collapse legacy mirror onto the same temp dir for tests

    patches = [
        patch.object(cfg, "WORKSPACE_DIR", ws_dir),
        patch.object(cfg, "LEGACY_WORKSPACE_DIR", legacy),
        patch.object(ws_mod, "WORKSPACE_DIR", ws_dir),
        patch.object(ws_mod, "LEGACY_WORKSPACE_DIR", legacy),
        patch.object(mgr_mod, "WORKSPACE_DIR", ws_dir),
        patch.object(mgr_mod, "LEGACY_WORKSPACE_DIR", legacy),
    ]
    for p in patches:
        p.start()
    try:
        yield ws_dir
    finally:
        for p in reversed(patches):
            p.stop()


def test_create_agent_seeds_three_per_agent_identity_files(AXIOM_db, _isolate_AXIOM_home):
    home = _isolate_AXIOM_home
    with _pin_workspace_dir(home) as ws_dir:
        from axiom.agents.manager import create_agent

        create_agent(
            agent_id="quant-researcher",
            name="Quant Researcher",
            role="Research market structure and own data integrity.",
            instructions="Read LESSONS.md before proposing anything.",
        )

        agent_dir = ws_dir / "agents" / "quant-researcher"
        for filename in ("SOUL.md", "AGENTS.md", "ROLE.md"):
            path = agent_dir / filename
            assert path.exists(), f"{filename} should exist for the agent"
            text = path.read_text(encoding="utf-8")
            assert text.strip(), f"{filename} should be non-empty"

        # Personalization: each file carries the agent's name.
        assert "Quant Researcher" in (agent_dir / "SOUL.md").read_text(encoding="utf-8")
        assert "Quant Researcher" in (agent_dir / "AGENTS.md").read_text(encoding="utf-8")
        assert "Quant Researcher" in (agent_dir / "ROLE.md").read_text(encoding="utf-8")
        # SOUL/AGENTS carry the shared template body too.
        assert "Axiom" in (agent_dir / "SOUL.md").read_text(encoding="utf-8")


def test_build_agent_documents_returns_per_agent_content(AXIOM_db, _isolate_AXIOM_home):
    home = _isolate_AXIOM_home
    with _pin_workspace_dir(home):
        from axiom.agents.manager import create_agent

        create_agent(
            agent_id="risk-manager",
            name="Risk Manager",
            role="Enforce capital preservation rules.",
            instructions="10% drawdown kill switch.",
        )

        # _build_agent_documents reads read_workspace, which honors the pinned
        # workspace dir.
        from axiom.api_core import _build_agent_documents

        docs = _build_agent_documents("risk-manager")
        assert docs["soul"].strip()
        assert docs["agents"].strip()
        assert docs["role"].strip()
        # The content is the agent-specific copy, not a bare global file.
        assert "Risk Manager" in docs["soul"]
        assert "Risk Manager" in docs["agents"]
        assert "Risk Manager" in docs["role"]
        assert "Enforce capital preservation rules." in docs["role"]


def test_ensure_identity_files_is_idempotent_and_self_heals(AXIOM_db, _isolate_AXIOM_home):
    """Self-heal recreates only MISSING files and never clobbers real content."""
    home = _isolate_AXIOM_home
    with _pin_workspace_dir(home) as ws_dir:
        from axiom.agents.manager import create_agent, ensure_agent_identity_files

        create_agent(
            agent_id="execution-trader",
            name="Execution Trader",
            role="Execute trades on HyperLiquid testnet.",
        )

        agent_dir = ws_dir / "agents" / "execution-trader"
        soul_path = agent_dir / "SOUL.md"

        # Operator-customized SOUL should survive a self-heal pass untouched.
        soul_path.write_text("# CUSTOM SOUL\nDo not overwrite me.\n", encoding="utf-8")

        # Delete AGENTS.md so the heal pass must recreate exactly one file.
        (agent_dir / "AGENTS.md").unlink()

        written = ensure_agent_identity_files(
            "execution-trader",
            "Execution Trader",
            "Execute trades on HyperLiquid testnet.",
        )

        assert written == ["agents/execution-trader/AGENTS.md"]
        assert "Do not overwrite me." in soul_path.read_text(encoding="utf-8")
        assert (agent_dir / "AGENTS.md").read_text(encoding="utf-8").strip()

        # A second pass with everything present is a no-op.
        assert ensure_agent_identity_files(
            "execution-trader",
            "Execution Trader",
            "Execute trades on HyperLiquid testnet.",
        ) == []


def test_put_agent_document_writes_per_agent_not_global(AXIOM_db, _isolate_AXIOM_home):
    home = _isolate_AXIOM_home
    with _pin_workspace_dir(home) as ws_dir:
        from axiom.agents.manager import create_agent
        from axiom.api_core import (
            LegacyAgentDocumentBody,
            put_agent_document,
            _build_agent_documents,
        )

        create_agent(
            agent_id="simulation-agent",
            name="Simulation Agent",
            role="Stress-test strategies.",
        )

        put_agent_document(
            "simulation-agent",
            "soul",
            LegacyAgentDocumentBody(content="# EDITED SOUL\nSim-specific soul."),
        )

        # Written to the per-agent path...
        per_agent = (ws_dir / "agents" / "simulation-agent" / "SOUL.md").read_text(encoding="utf-8")
        assert "Sim-specific soul." in per_agent
        # ...and surfaced back through _build_agent_documents.
        assert "Sim-specific soul." in _build_agent_documents("simulation-agent")["soul"]
        # The shared global SOUL.md must NOT have been overwritten by the edit.
        global_soul = ws_dir / "SOUL.md"
        if global_soul.exists():
            assert "Sim-specific soul." not in global_soul.read_text(encoding="utf-8")
