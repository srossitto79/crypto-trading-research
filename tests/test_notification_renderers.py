from axiom.bot import _render_operational_discord_reply
from axiom.notification_renderers import render_discord_message, summarize_discord_text


SAMPLE_POST_MORTEM = """My Findings
There's no "Post-Mortem: S00011 failure" task in the system. I queried the database and found:

Closest match: Task #1049 is "Post-Mortem: S00178 failure" (marked reviewed) \u2014 that's the risk-manager output you saw
S00011 exists in the STRATEGIES list as [archived] SOL-EMA_CROSS-S00011 \u2014 but it was archived, not rejected via gauntlet failure

What IS in the pipeline right now:
| Task | Agent | Status |
|------|-------|--------|
| 1050 | quant-researcher | running \u2014 Ideation: Generate Strategy Hypotheses |

The Actual Issue You Should Focus On
The risk-manager task (#1049) already gave us the root cause:

Primary Failure Cause: The strategy container transitioned to gauntlet without any backtest results being generated or persisted.

The 5 pending approvals (#114-118) are all related to fixing this \u2014 they need the spread_arbitrage strategy type registered so S00178 can actually be validated.

What would you like me to do?
Approve one of the spread_arbitrage fix requests so validation can proceed?
Something else?

\u2014 Axiom"""


def test_summarize_discord_text_keeps_high_signal_lines():
    rendered = summarize_discord_text(SAMPLE_POST_MORTEM)

    assert rendered is not None
    assert "Closest match:" in rendered
    assert "Primary Failure Cause:" in rendered
    assert "pending approvals" in rendered
    assert "What would you like me to do" not in rendered
    assert "| Task | Agent | Status |" not in rendered


def test_render_operational_discord_reply_uses_task_title_for_agent_callbacks():
    rendered = _render_operational_discord_reply(
        SAMPLE_POST_MORTEM,
        source="agent_callback",
        task_message=(
            "Agent risk-manager just completed task 'Post-Mortem: S00178 failure'. "
            "Review their output in the COMPLETED AGENT TASKS section and take any necessary next steps."
        ),
    )

    assert rendered.startswith("Review complete: Post-Mortem: S00178 failure")
    assert "Primary Failure Cause:" in rendered
    assert "What would you like me to do" not in rendered


def test_render_discord_message_brain_response_uses_compact_body():
    rendered = render_discord_message(
        {
            "event_type": "brain_response",
            "title": "Brain response ready",
            "body": SAMPLE_POST_MORTEM,
            "metadata": {},
        }
    )

    assert "Brain response ready" in rendered
    assert "Primary Failure Cause:" in rendered
    assert "pending approvals" in rendered
    assert "Full context lives in the app" in rendered
    assert "What would you like me to do" not in rendered
