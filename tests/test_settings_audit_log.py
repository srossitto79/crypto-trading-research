from axiom.api_core import _diff_settings_section, _append_settings_audit


def test_diff_flat_change():
    old = {"max_daily_loss": 200, "max_drawdown_pct": 30}
    new = {"max_daily_loss": 150, "max_drawdown_pct": 30}
    entries = _diff_settings_section("risk", old, new, actor="test")
    assert len(entries) == 1
    e = entries[0]
    assert e["id"] == "risk.max_daily_loss"
    assert e["from"] == 200
    assert e["to"] == 150
    assert e["actor"] == "test"
    assert "at" in e


def test_diff_ignores_unchanged():
    old = {"a": 1, "b": 2}
    new = {"a": 1, "b": 3}
    entries = _diff_settings_section("risk", old, new)
    assert [e["id"] for e in entries] == ["risk.b"]


def test_diff_nested_change():
    old = {"quick_screen": {"min_sharpe": 0.5}}
    new = {"quick_screen": {"min_sharpe": 0.8}}
    entries = _diff_settings_section("pipeline", old, new)
    assert [e["id"] for e in entries] == ["pipeline.quick_screen.min_sharpe"]


def test_diff_ignores_volatile_keys():
    old = {"updated_at": "t1", "audit_log": [], "max_daily_loss": 200}
    new = {"updated_at": "t2", "audit_log": [{"x": 1}], "max_daily_loss": 150}
    entries = _diff_settings_section("risk", old, new)
    assert [e["id"] for e in entries] == ["risk.max_daily_loss"]


def test_append_trims_to_50():
    log = [{"id": f"x.{i}", "from": 0, "to": 1, "at": "t", "actor": "a"} for i in range(50)]
    new_entry = {"id": "x.new", "from": 0, "to": 1, "at": "t", "actor": "a"}
    result = _append_settings_audit(log, [new_entry])
    assert len(result) == 50
    assert result[-1]["id"] == "x.new"
    assert result[0]["id"] == "x.1"  # oldest evicted


def test_put_settings_section_writes_audit(AXIOM_db):
    """End-to-end: a real PUT appends an entry to the persisted audit log."""
    import axiom.api_core as core

    core._save_settings_payload(core._default_settings_payload())

    from axiom.api_core import put_settings_section, get_settings

    put_settings_section("risk", {"max_daily_loss": 150})
    settings = get_settings()

    audit = settings.get("audit_log") or []
    assert any(e["id"] == "risk.max_daily_loss" and e["to"] == 150 for e in audit)


def test_get_settings_audit_log_returns_newest_first(AXIOM_db):
    from axiom.api_core import put_settings_section, get_settings_audit_log

    put_settings_section("risk", {"max_daily_loss": 111})
    put_settings_section("risk", {"max_daily_loss": 222})

    log = get_settings_audit_log(limit=5)
    assert isinstance(log, list)
    # Find the two risk.max_daily_loss entries (other entries may also be present from the put).
    risk_entries = [e for e in log if e["id"] == "risk.max_daily_loss"]
    assert len(risk_entries) >= 2
    assert risk_entries[0]["to"] == 222  # newest first
    assert risk_entries[1]["to"] == 111


def test_get_settings_audit_log_respects_limit(AXIOM_db):
    from axiom.api_core import put_settings_section, get_settings_audit_log

    for n in range(10):
        put_settings_section("risk", {"max_daily_loss": 100 + n})

    assert len(get_settings_audit_log(limit=3)) == 3
    # limit <= 0 returns full log
    full = get_settings_audit_log(limit=0)
    assert len(full) >= 10
