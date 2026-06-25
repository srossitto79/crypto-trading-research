"""Backtesting client configuration resolution tests."""

from __future__ import annotations


def _clear_backtesting_env(monkeypatch):
    for key in (
        "AXIOM_BACKTEST_API",
        "AXIOM_BACKTEST_API_URL",
        "AXIOM_BACKTESTING_API_URL",
        "AXIOM_BACKTEST_BASE_URL",
        "AXIOM_BACKTEST_BASE",
        "AXIOM_BACKTEST_REMOTE_API",
        "AXIOM_BACKTEST_RESULTS_REMOTE_API",
        "AXIOM_CLIENT_BASE",
        "AXIOM_PORT",
    ):
        monkeypatch.delenv(key, raising=False)


def test_backtesting_default_uses_local_8003(monkeypatch):
    import axiom.backtesting as bt

    _clear_backtesting_env(monkeypatch)
    resolved = bt._resolve_backtesting_api_base_url()
    assert resolved == "http://127.0.0.1:8003/api"


def test_backtesting_uses_client_base_when_set(monkeypatch):
    import axiom.backtesting as bt

    _clear_backtesting_env(monkeypatch)
    monkeypatch.setenv("AXIOM_CLIENT_BASE", "http://127.0.0.1:8123")
    resolved = bt._resolve_backtesting_api_base_url()
    assert resolved == "http://127.0.0.1:8123/api"


def test_backtesting_env_has_precedence_over_client_base(monkeypatch):
    import axiom.backtesting as bt

    _clear_backtesting_env(monkeypatch)
    monkeypatch.setenv("AXIOM_CLIENT_BASE", "http://127.0.0.1:8123")
    monkeypatch.setenv("AXIOM_BACKTEST_API", "http://127.0.0.1:9001")
    resolved = bt._resolve_backtesting_api_base_url()
    assert resolved == "http://127.0.0.1:9001/api"


def test_get_client_rebinds_on_base_url_change(monkeypatch):
    import axiom.backtesting as bt

    _clear_backtesting_env(monkeypatch)
    monkeypatch.setenv("AXIOM_BACKTEST_API", "http://127.0.0.1:8003")
    bt._client = None
    client_one = bt.get_client()
    assert client_one.base_url == "http://127.0.0.1:8003/api"

    monkeypatch.setenv("AXIOM_BACKTEST_API", "http://127.0.0.1:8011")
    client_two = bt.get_client()
    assert client_two.base_url == "http://127.0.0.1:8011/api"
    assert client_two is not client_one

    # Cleanup explicit handles created in this test.
    try:
        client_one.close()
    except Exception:
        pass
    try:
        client_two.close()
    except Exception:
        pass
    bt._client = None
