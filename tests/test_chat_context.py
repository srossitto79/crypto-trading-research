"""Chat-context grounding + conversation persistence tests."""
from __future__ import annotations

import asyncio
import json

import axiom.context as ctx


# ---------------------------------------------------------------------------
# build_chat_context grounding blocks
# ---------------------------------------------------------------------------

def test_build_chat_context_includes_pipeline_and_pending_approvals(monkeypatch):
    """Chat context should surface a condensed pipeline + pending-approvals block
    so the Brain can answer 'what's in the pipeline?' / 'anything on me?'."""

    # Keep the rest of the context cheap/empty so the test is deterministic.
    monkeypatch.setattr(ctx, "read_workspace", lambda *a, **k: "", raising=True)
    monkeypatch.setattr(ctx, "_render_operator_profile", lambda: None, raising=True)
    monkeypatch.setattr(ctx, "_format_portfolio_status", lambda: "", raising=True)
    monkeypatch.setattr(ctx, "_format_strategy_registry", lambda: "", raising=True)
    monkeypatch.setattr(ctx, "_format_market_regime", lambda: "", raising=True)
    monkeypatch.setattr(ctx, "_format_recent_trades", lambda *a, **k: "", raising=True)

    monkeypatch.setattr(
        ctx,
        "_format_evolution_status",
        lambda: "# EVOLUTION PIPELINE\n- gauntlet: 2 (S00719, S00825)",
        raising=True,
    )
    monkeypatch.setattr(
        ctx,
        "_format_recent_approval_feedback",
        lambda limit=20: (
            "# APPROVAL FEEDBACK\n"
            "- [PENDING_APPROVAL] #5 strategy/S00719 (promotion) — paper -> live\n"
            "- [APPROVED] #4 strategy/S00200 (promotion)"
        ),
        raising=True,
    )

    out = ctx.build_chat_context()
    assert "# EVOLUTION PIPELINE" in out
    assert "# PENDING APPROVALS (waiting on you)" in out
    # Only the pending entry should survive the compaction.
    assert "#5 strategy/S00719" in out
    assert "#4 strategy/S00200" not in out


def test_pending_approvals_compact_empty_when_none(monkeypatch):
    monkeypatch.setattr(
        ctx,
        "_format_recent_approval_feedback",
        lambda limit=20: "# APPROVAL FEEDBACK\n- [APPROVED] #1 strategy/S1 (promotion)",
        raising=True,
    )
    assert ctx._format_pending_approvals_compact() == ""


def test_pending_approvals_compact_caps_and_summarizes(monkeypatch):
    lines = ["# APPROVAL FEEDBACK"]
    for i in range(12):
        lines.append(f"- [PENDING_APPROVAL] #{i} strategy/S{i} (promotion)")
    monkeypatch.setattr(
        ctx,
        "_format_recent_approval_feedback",
        lambda limit=20: "\n".join(lines),
        raising=True,
    )
    out = ctx._format_pending_approvals_compact(limit=8)
    pending_rendered = [ln for ln in out.splitlines() if "[PENDING_APPROVAL]" in ln]
    assert len(pending_rendered) == 8
    assert "and 4 more" in out


# ---------------------------------------------------------------------------
# store_conversation — source parameterization + Discord backward-compat
# ---------------------------------------------------------------------------

class _Captured:
    def __init__(self):
        self.summary = None
        self.metadata = None


def _patch_store_narrative(monkeypatch):
    captured = _Captured()

    def _fake_store_narrative(summary, metadata=None):
        captured.summary = summary
        captured.metadata = metadata

    import axiom.vectordb as vdb

    monkeypatch.setattr(vdb, "store_narrative", _fake_store_narrative, raising=True)
    return captured


def test_store_conversation_default_source_is_ui_chat(monkeypatch):
    captured = _patch_store_narrative(monkeypatch)
    asyncio.run(
        ctx.store_conversation(
            user_msg="How is S00719 doing in the gauntlet?",
            ai_response="It is mid-gauntlet with a Sharpe near 1.4 on the OOS window. — Axiom",
        )
    )
    assert captured.metadata is not None
    assert captured.metadata["source"] == "ui_chat"
    assert captured.summary.startswith("[ui_chat]")
    assert "channel" not in captured.metadata


def test_store_conversation_explicit_ui_chat_source(monkeypatch):
    captured = _patch_store_narrative(monkeypatch)
    asyncio.run(
        ctx.store_conversation(
            None,
            "What is in the pipeline right now exactly?",
            "Two candidates in gauntlet and one paper strategy. — Axiom",
            source="ui_chat",
        )
    )
    assert captured.metadata["source"] == "ui_chat"
    assert captured.summary.startswith("[ui_chat]")


def test_store_conversation_discord_backward_compat(monkeypatch):
    """The legacy Discord caller passes a channel_name and NO source — it must
    still persist with the '[Discord #...]' prefix and source='discord'."""
    captured = _patch_store_narrative(monkeypatch)
    asyncio.run(
        ctx.store_conversation(
            "general",
            "How are we doing today on the books?",
            "Equity is flat, no open positions, regime is range-bound. — Axiom",
        )
    )
    assert captured.metadata["source"] == "discord"
    assert captured.metadata["channel"] == "general"
    assert captured.summary.startswith("[Discord #general]")


def test_store_conversation_explicit_discord_source(monkeypatch):
    captured = _patch_store_narrative(monkeypatch)
    asyncio.run(
        ctx.store_conversation(
            "alerts",
            "Did the kill switch trip overnight on the account?",
            "No, drawdown stayed under 2% the whole session. — Axiom",
            source="discord",
        )
    )
    assert captured.metadata["source"] == "discord"
    assert captured.summary.startswith("[Discord #alerts]")


def test_store_conversation_skips_trivial_exchanges(monkeypatch):
    captured = _patch_store_narrative(monkeypatch)
    asyncio.run(ctx.store_conversation(None, "hi", "ok", source="ui_chat"))
    # Below the length threshold — nothing persisted.
    assert captured.summary is None
    assert captured.metadata is None


# ---------------------------------------------------------------------------
# runtime_worker is_chat branch — CHAT_ACT toolset wiring + persistence
# ---------------------------------------------------------------------------

def test_run_brain_task_chat_uses_act_toolset_and_persists(AXIOM_db, monkeypatch):
    from axiom import runtime_worker
    from axiom.agents.tool_definitions import CHAT_ACT_TOOL_NAMES

    captured: dict = {}

    async def _fake_call_with_tools(provider, model, messages, context, tools=None):
        captured["tools"] = tools
        captured["last_message"] = messages[-1]["content"]
        return ("Looks healthy. — Axiom", {})

    stored: dict = {}

    async def _fake_store_conversation(channel_name=None, user_msg="", ai_response="", source=None):
        stored["channel_name"] = channel_name
        stored["user_msg"] = user_msg
        stored["ai_response"] = ai_response
        stored["source"] = source

    monkeypatch.setattr("axiom.context.build_chat_context", lambda: "ctx")
    monkeypatch.setattr("axiom.context.store_conversation", _fake_store_conversation)
    monkeypatch.setattr("axiom.brain.resolve_brain_provider_model", lambda p, m: ("openai", "gpt-5.2"))
    monkeypatch.setattr("axiom.agents.runner._call_with_tools", _fake_call_with_tools)
    monkeypatch.setattr("axiom.agents.runner.set_tool_context", lambda *a, **k: ())
    monkeypatch.setattr("axiom.agents.runner.reset_tool_context", lambda *_: None)

    task = {
        "id": 7,
        "payload": json.dumps(
            {
                "source": "ui_chat",
                "message": "How is S00719 doing in the gauntlet right now?",
            }
        ),
    }

    asyncio.run(runtime_worker._run_brain_task(task))

    # The chat branch must offer the CHAT_ACT tool tier (action-capable Command mode).
    tool_names = {t["name"] for t in (captured["tools"] or [])}
    assert tool_names, "expected a non-empty chat tool list"
    assert tool_names <= CHAT_ACT_TOOL_NAMES, f"unexpected tools leaked: {tool_names - CHAT_ACT_TOOL_NAMES}"
    assert "assign_agent_task" in tool_names  # an action tool reachable in Command mode
    assert "read_file" in tool_names           # a grounding tool

    # The exchange is persisted for recall with the ui_chat source.
    assert stored["source"] == "ui_chat"
    assert stored["user_msg"] == "How is S00719 doing in the gauntlet right now?"
    assert "Axiom" in stored["ai_response"]
