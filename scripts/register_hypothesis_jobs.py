"""One-shot script to register the hypothesis scheduler jobs. Idempotent.

Jobs are registered ENABLED. Without these jobs running, the active-pool cap
silently traps the system: hypotheses never receive verdicts, never graduate,
and never free their slot — so new hypotheses get refused indefinitely.
"""
from axiom.scheduler import add_job, enable_job


def main() -> None:
    add_job(
        job_id="Axiom-hypothesis-verdict-loop",
        name="Hypothesis verdict loop (LLM memo trigger)",
        schedule_type="interval",
        schedule_expr="300000",  # 5 min in ms
        command="hypothesis_verdict_loop",
        payload={"kind": "hypothesis_verdict_loop", "max_per_tick": 10},
    )
    enable_job("Axiom-hypothesis-verdict-loop", enabled=True)

    add_job(
        job_id="Axiom-hypothesis-promotion-loop",
        name="Hypothesis promotion loop (pick top-K and dispatch)",
        schedule_type="interval",
        schedule_expr="300000",  # 5 min in ms
        command="hypothesis_promotion_loop",
        payload={"kind": "hypothesis_promotion_loop", "top_k": 3, "max_in_flight": 5},
    )
    enable_job("Axiom-hypothesis-promotion-loop", enabled=True)

    add_job(
        job_id="Axiom-hypothesis-revisit-pass",
        name="Hypothesis revisit pass (graduated → active when due)",
        schedule_type="interval",
        schedule_expr="86400000",  # 24 h in ms
        command="hypothesis_revisit_pass",
        payload={"kind": "hypothesis_revisit_pass"},
    )
    enable_job("Axiom-hypothesis-revisit-pass", enabled=True)


if __name__ == "__main__":
    main()
