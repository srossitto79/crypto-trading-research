"""2026-06-13 — extract_insight dropped 100% of insights (165/165 one night) when the
model's JSON was fenced, prefixed with prose, or truncated. _parse_insight_json now
tolerates fences and recovers the outermost {...} object; the action allowlist still
gates partial dicts."""
import axiom.quant_skills_extractor as qse


def test_parse_clean_json():
    out = qse._parse_insight_json('{"action": "skip", "pattern": "x"}')
    assert out == {"action": "skip", "pattern": "x"}


def test_parse_fenced_json():
    text = '```json\n{"action": "update_skill", "skill_name": "rsi-mr"}\n```'
    assert qse._parse_insight_json(text) == {"action": "update_skill", "skill_name": "rsi-mr"}


def test_parse_prose_wrapped_json_recovers_via_brace_slice():
    text = 'Here is the analysis you asked for:\n{"action": "new_hypothesis", "pattern": "vol-expansion"}\nHope that helps!'
    assert qse._parse_insight_json(text) == {"action": "new_hypothesis", "pattern": "vol-expansion"}


def test_parse_truncated_json_returns_none():
    # Genuinely unclosed/invalid — no balanced object to recover.
    assert qse._parse_insight_json('{"action": "update_skill", "observation": "the sharpe was') is None


def test_parse_non_dict_json_returns_none():
    assert qse._parse_insight_json('["not", "a", "dict"]') is None
    assert qse._parse_insight_json("") is None


def test_extract_insight_recovers_prose_wrapped_response(monkeypatch):
    monkeypatch.setattr(
        "axiom.model_routing.get_auxiliary_routing",
        lambda _task: {"provider": "openai", "model_id": "gpt-x"},
    )
    monkeypatch.setattr(
        "axiom.ai.call_ai_sync",
        lambda **_kwargs: 'Sure!\n{"action": "skip", "observation": "unremarkable"}\n',
    )
    out = qse.extract_insight({"metrics": {"total_trades": 5}}, [])
    assert out is not None
    assert out["action"] == "skip"


def test_extract_insight_rejects_bad_action_after_recovery(monkeypatch):
    monkeypatch.setattr(
        "axiom.model_routing.get_auxiliary_routing",
        lambda _task: {"provider": "openai", "model_id": "gpt-x"},
    )
    monkeypatch.setattr(
        "axiom.ai.call_ai_sync",
        lambda **_kwargs: '{"action": "delete_everything", "pattern": "evil"}',
    )
    # Recovered dict carries an action outside the allowlist -> rejected.
    assert qse.extract_insight({"metrics": {"total_trades": 5}}, []) is None


def test_extract_insight_returns_none_on_unparseable(monkeypatch):
    monkeypatch.setattr(
        "axiom.model_routing.get_auxiliary_routing",
        lambda _task: {"provider": "openai", "model_id": "gpt-x"},
    )
    monkeypatch.setattr("axiom.ai.call_ai_sync", lambda **_kwargs: "not json at all, sorry")
    assert qse.extract_insight({"metrics": {"total_trades": 5}}, []) is None
