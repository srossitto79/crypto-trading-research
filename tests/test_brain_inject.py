"""Phase 1 (P1-T04) — fenced user-message injection tests.

Verifies the constant-shape brain-context fence, byte-equal leading text
across consecutive cycles with identical memory, prompt-hash sensitivity
to memory mutations, and the ``brain_cache_hit_rate`` diagnostics check.
"""
from __future__ import annotations

from axiom import brain_memory, brain_inject
from axiom.diagnostics import check_brain_cache_hit_rate


def _set_memory(AXIOM_db, body: str) -> None:
    brain_memory.set_memory(body, mutated_by="test")


def test_empty_memory_still_emits_well_formed_fence(AXIOM_db):
    block = brain_inject.build_brain_context_block("")
    assert block.startswith(brain_inject.BRAIN_CONTEXT_OPEN)
    assert block.endswith(brain_inject.BRAIN_CONTEXT_CLOSE)
    assert brain_inject.BRAIN_CONTEXT_GUARD in block


def test_user_message_places_fence_before_situational_text(AXIOM_db):
    msg = brain_inject.build_user_message(
        situational_text="hourly cycle prompt body",
        memory_body="remember alpha",
    )
    assert msg.startswith(brain_inject.BRAIN_CONTEXT_OPEN)
    fence_close = msg.index(brain_inject.BRAIN_CONTEXT_CLOSE)
    situational_start = msg.index("hourly cycle prompt body")
    assert situational_start > fence_close


def test_extract_leading_user_text_stops_at_boundary(AXIOM_db):
    msg = brain_inject.build_user_message(
        situational_text="situational TAIL text",
        memory_body="m1",
    )
    leading = brain_inject.extract_leading_user_text(msg)
    assert leading.endswith(brain_inject.BRAIN_CONTEXT_CLOSE)
    assert "TAIL" not in leading


def test_consecutive_cycles_with_identical_memory_share_leading_text(AXIOM_db):
    _set_memory(AXIOM_db, "stable memory body")
    msg_a = brain_inject.build_user_message(
        situational_text="cycle A situational text differs",
        memory_body=brain_inject.get_memory_body_for_injection(),
    )
    msg_b = brain_inject.build_user_message(
        situational_text="cycle B situational text differs MORE",
        memory_body=brain_inject.get_memory_body_for_injection(),
    )
    leading_a = brain_inject.extract_leading_user_text(msg_a)
    leading_b = brain_inject.extract_leading_user_text(msg_b)
    assert leading_a == leading_b  # byte-equal
    assert leading_a.encode("utf-8") == leading_b.encode("utf-8")


def test_prompt_hash_stable_when_memory_unchanged(AXIOM_db):
    _set_memory(AXIOM_db, "stable")
    system = "constant-system-prompt"
    msg_a = brain_inject.build_user_message("variable A", brain_inject.get_memory_body_for_injection())
    msg_b = brain_inject.build_user_message("variable B which is much longer text", brain_inject.get_memory_body_for_injection())
    assert brain_inject.compute_prompt_hash(system, msg_a) == brain_inject.compute_prompt_hash(system, msg_b)


def test_prompt_hash_changes_when_memory_mutates(AXIOM_db):
    _set_memory(AXIOM_db, "first")
    msg_a = brain_inject.build_user_message("x", brain_inject.get_memory_body_for_injection())
    _set_memory(AXIOM_db, "second")
    msg_b = brain_inject.build_user_message("x", brain_inject.get_memory_body_for_injection())
    assert brain_inject.compute_prompt_hash("sys", msg_a) != brain_inject.compute_prompt_hash("sys", msg_b)


def test_prompt_hash_changes_when_system_changes(AXIOM_db):
    _set_memory(AXIOM_db, "stable")
    msg = brain_inject.build_user_message("x", brain_inject.get_memory_body_for_injection())
    h1 = brain_inject.compute_prompt_hash("sys-A", msg)
    h2 = brain_inject.compute_prompt_hash("sys-B", msg)
    assert h1 != h2


def test_record_cache_observation_counts_hits_and_misses(AXIOM_db):
    h1 = "a" * 64
    h2 = "b" * 64
    s = brain_inject.record_cache_observation(h1)
    assert s["hits"] == 0 and s["misses"] == 0  # first cycle: no comparison
    s = brain_inject.record_cache_observation(h1)
    assert s["hits"] == 1 and s["misses"] == 0
    s = brain_inject.record_cache_observation(h2)
    assert s["hits"] == 1 and s["misses"] == 1
    s = brain_inject.record_cache_observation(h2)
    assert s["hits"] == 2 and s["misses"] == 1
    snap = brain_inject.cache_hit_rate_snapshot()
    assert snap["comparisons"] == 3
    assert snap["rate"] == 2 / 3


def test_diagnostics_check_reports_rate_after_two_cycles(AXIOM_db):
    _set_memory(AXIOM_db, "a")
    msg_a = brain_inject.build_user_message("cycle1", brain_inject.get_memory_body_for_injection())
    msg_b = brain_inject.build_user_message("cycle2", brain_inject.get_memory_body_for_injection())
    h1 = brain_inject.compute_prompt_hash("sys", msg_a)
    h2 = brain_inject.compute_prompt_hash("sys", msg_b)
    brain_inject.record_cache_observation(h1)
    brain_inject.record_cache_observation(h2)
    res = check_brain_cache_hit_rate()
    assert res.name == "brain_cache_hit_rate"
    assert res.status in {"pass", "warn"}
    assert isinstance(res.detail.get("rate"), (int, float))


def test_diagnostics_check_passes_with_no_history(AXIOM_db):
    res = check_brain_cache_hit_rate()
    assert res.status == "pass"
    assert res.detail.get("comparisons") == 0


def test_fence_constants_are_unique_and_searchable(AXIOM_db):
    assert brain_inject.BRAIN_CONTEXT_OPEN == "<brain-context>"
    assert brain_inject.BRAIN_CONTEXT_CLOSE == "</brain-context>"
    assert brain_inject.BRAIN_CONTEXT_BOUNDARY == brain_inject.BRAIN_CONTEXT_CLOSE
