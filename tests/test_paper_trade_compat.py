from axiom.api_domains import paper as paper_domain


def test_build_compat_paper_trade_marks_closed_rows_without_exit_details_incomplete():
    trade = paper_domain._build_compat_paper_trade(
        {
            "id": "E9991",
            "direction": "long",
            "entry_price": 100.0,
            "size": 1.0,
            "leverage": 1.0,
            "opened_at": "2026-03-10T15:00:00+00:00",
            "closed_at": "2026-03-10T15:05:00+00:00",
            "exit_price": None,
            "fill_exit_price": None,
            "signal_exit_price": None,
            "pnl_usd": None,
            "pnl_pct": None,
        },
        strategy_name="Compat Strategy",
        symbol="BTC/USDT",
    )

    assert trade["exit_price"] is None
    assert trade["pnl"] is None
    assert trade["pnl_pct"] is None
    assert trade["net_pnl"] is None
    assert trade["net_pnl_pct"] is None
    assert trade["close_incomplete"] is True
    assert trade["close_reason"] is None


def test_build_compat_paper_trade_uses_exit_fallbacks_and_computes_pnl():
    trade = paper_domain._build_compat_paper_trade(
        {
            "id": "E9992",
            "direction": "short",
            "entry_price": 100.0,
            "size": 2.0,
            "leverage": 1.5,
            "opened_at": "2026-03-10T15:00:00+00:00",
            "closed_at": "2026-03-10T15:05:00+00:00",
            "exit_price": None,
            "fill_exit_price": 95.0,
            "signal_exit_price": None,
            "pnl_usd": None,
            "pnl_pct": None,
        },
        strategy_name="Compat Strategy",
        symbol="BTC/USDT",
    )

    assert trade["exit_price"] == 95.0
    # PAPER-1: dollar PnL excludes leverage (short (95-100)*2.0*-1 = 10.0, not
    # 10.0 * leverage 1.5 = 15.0); pnl_pct still carries leverage (7.5%).
    assert trade["pnl"] == 10.0
    assert abs(float(trade["pnl_pct"]) - 7.5) < 1e-9


def test_build_compat_paper_trade_surfaces_incomplete_close_metadata():
    trade = paper_domain._build_compat_paper_trade(
        {
            "id": "E9993",
            "direction": "long",
            "entry_price": 100.0,
            "size": 1.0,
            "leverage": 1.0,
            "opened_at": "2026-03-10T15:00:00+00:00",
            "closed_at": "2026-03-10T15:05:00+00:00",
            "exit_price": None,
            "fill_exit_price": None,
            "signal_exit_price": None,
            "pnl_usd": None,
            "pnl_pct": None,
            "signal_data": {"close_reason": "reconcile_missing_on_exchange", "close_incomplete": True},
        },
        strategy_name="Compat Strategy",
        symbol="BTC/USDT",
    )

    assert trade["exit_price"] is None
    assert trade["close_incomplete"] is True
    assert trade["close_reason"] == "reconcile_missing_on_exchange"
