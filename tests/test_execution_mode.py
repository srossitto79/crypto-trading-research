import pytest

from axiom.config import (
    get_execution_mode,
    load_config,
    save_config,
    set_execution_mode,
)


def _force_config_mode(mode: str) -> None:
    """Seed config.json execution_mode directly, bypassing the supported-write
    guard. Simulates a user who manually forces an unsupported mode at their own
    risk — ``set_execution_mode`` itself refuses anything but ``paper``.
    """
    cfg = load_config()
    cfg["execution_mode"] = mode
    save_config(cfg)


class TestExecutionMode:
    """Execution mode read/write. Live/mainnet is NOT a supported feature."""

    def test_default_is_paper(self):
        assert get_execution_mode() == "paper"

    def test_set_paper_round_trips(self):
        set_execution_mode("paper")
        assert get_execution_mode() == "paper"

    def test_set_live_is_rejected(self):
        # The supported write-site refuses live/mainnet unconditionally.
        with pytest.raises(ValueError, match="Unsupported execution mode"):
            set_execution_mode("live")

    def test_env_overrides_config(self, monkeypatch):
        # A manually-forced 'live' config is honored on read, but an env var
        # still takes precedence.
        _force_config_mode("live")
        monkeypatch.setenv("AXIOM_EXECUTION_MODE", "paper")
        assert get_execution_mode() == "paper"

    def test_forced_live_config_is_honored_on_read(self):
        # The read path still surfaces a manually-forced 'live' so the
        # fail-closed Rule 0c margin guard (exchange/risk.py) applies to it.
        _force_config_mode("live")
        assert get_execution_mode() == "live"


class TestBetaPaperLock:
    """AXIOM_ENV=beta hard-locks execution mode to paper (packaged builds)."""

    def test_set_live_rejected_in_beta_too(self, monkeypatch):
        monkeypatch.setenv("AXIOM_ENV", "beta")
        with pytest.raises(ValueError, match="Unsupported execution mode"):
            set_execution_mode("live")

    def test_beta_build_still_allows_paper(self, monkeypatch):
        monkeypatch.setenv("AXIOM_ENV", "beta")
        set_execution_mode("paper")
        assert get_execution_mode() == "paper"

    def test_beta_build_forces_paper_on_read_even_if_config_says_live(
        self, monkeypatch
    ):
        # Simulate a stale config.json with a forced 'live' value.
        _force_config_mode("live")
        assert get_execution_mode() == "live"
        monkeypatch.setenv("AXIOM_ENV", "beta")
        assert get_execution_mode() == "paper"
