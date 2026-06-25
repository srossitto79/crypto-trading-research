

def test_bootstrap_copies_default_env_when_missing(monkeypatch, tmp_path):
    home = tmp_path / "Axiom"
    monkeypatch.setenv("AXIOM_HOME", str(home))
    default_env = tmp_path / "default.env"
    default_env.write_text("AXIOM_ENV=beta\n")
    monkeypatch.setenv("AXIOM_DEFAULT_ENV", str(default_env))
    from axiom.config import ensure_state_dir_bootstrapped
    ensure_state_dir_bootstrapped()
    assert (home / ".env").read_text() == "AXIOM_ENV=beta\n"


def test_bootstrap_leaves_existing_env_alone(monkeypatch, tmp_path):
    home = tmp_path / "Axiom"
    home.mkdir()
    (home / ".env").write_text("AXIOM_ENV=custom\n")
    monkeypatch.setenv("AXIOM_HOME", str(home))
    default_env = tmp_path / "default.env"
    default_env.write_text("AXIOM_ENV=beta\n")
    monkeypatch.setenv("AXIOM_DEFAULT_ENV", str(default_env))
    from axiom.config import ensure_state_dir_bootstrapped
    ensure_state_dir_bootstrapped()
    assert (home / ".env").read_text() == "AXIOM_ENV=custom\n"
