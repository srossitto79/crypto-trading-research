"""Tests for Axiom.security.env_allowlist — subprocess env filter."""

from axiom.security.env_allowlist import build_subprocess_env


def test_path_passes_through():
    base = {"PATH": "/usr/bin:/bin", "OPENAI_API_KEY": "sk-secret"}
    env = build_subprocess_env(base=base)
    assert "PATH" in env
    assert env["PATH"] == "/usr/bin:/bin"


def test_secret_blocked_by_block_pattern():
    base = {
        "PATH": "/usr/bin",
        "OPENAI_API_KEY": "sk-secret-1234",
        "ANTHROPIC_API_KEY": "sk-ant-secret",
        "DATABASE_PASSWORD": "hunter2",
        "AWS_SECRET_ACCESS_KEY": "abc",
        "AUTH_TOKEN": "xyz",
    }
    env = build_subprocess_env(base=base)
    for blocked in [
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "DATABASE_PASSWORD",
        "AWS_SECRET_ACCESS_KEY",
        "AUTH_TOKEN",
    ]:
        assert blocked not in env


def test_random_var_blocked_by_default():
    """Names not in the allow list are dropped, even if not secret-shaped."""
    base = {"PATH": "/usr/bin", "FANCY_FEATURE_FLAG": "yes"}
    env = build_subprocess_env(base=base)
    assert "PATH" in env
    assert "FANCY_FEATURE_FLAG" not in env


def test_explicit_extra_bypasses_filter():
    """Caller-explicit additions are not filtered — they're trusted."""
    base = {"PATH": "/usr/bin"}
    env = build_subprocess_env(
        extra={"OPENAI_API_KEY": "needed-for-mcp-server"},
        base=base,
    )
    assert env["OPENAI_API_KEY"] == "needed-for-mcp-server"


def test_locale_vars_pass():
    base = {"PATH": "/x", "LANG": "en_US.UTF-8", "LC_ALL": "C", "LC_TIME": "en_US"}
    env = build_subprocess_env(base=base)
    assert env["LANG"] == "en_US.UTF-8"
    assert env["LC_ALL"] == "C"
    assert env["LC_TIME"] == "en_US"


def test_xdg_vars_pass():
    base = {"PATH": "/x", "XDG_CONFIG_HOME": "/tmp/.config", "XDG_DATA_HOME": "/tmp/.data"}
    env = build_subprocess_env(base=base)
    assert "XDG_CONFIG_HOME" in env
    assert "XDG_DATA_HOME" in env


def test_AXIOM_vars_pass():
    base = {"PATH": "/x", "AXIOM_HOME": "/home/x/.Axiom", "AXIOM_PROFILE": "default"}
    env = build_subprocess_env(base=base)
    assert env["AXIOM_HOME"] == "/home/x/.Axiom"
    assert env["AXIOM_PROFILE"] == "default"


def test_empty_base():
    env = build_subprocess_env(base={})
    assert env == {}


def test_uses_os_environ_by_default(monkeypatch):
    """When base is None, os.environ is the source."""
    monkeypatch.setenv("PATH", "/spam")
    monkeypatch.setenv("MY_FAKE_API_KEY", "should-be-blocked")
    env = build_subprocess_env()
    assert env.get("PATH") == "/spam"
    assert "MY_FAKE_API_KEY" not in env
