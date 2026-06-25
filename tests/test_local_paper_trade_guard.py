"""Lead-1 regression: the exchange-truth reconciler must not force-close
local-only paper trades. The discriminator is is_local_only_paper_trade — a
paper/paper_challenger trade with no exchange correlation id never reached the
exchange, so its absence there is not a ghost.
"""
from __future__ import annotations

import json

from axiom.trade_state import is_local_only_paper_trade, trade_reached_exchange


def test_local_paper_trade_is_protected():
    trade = {"id": "E0158", "execution_type": "paper", "signal_data": "{}"}
    assert is_local_only_paper_trade(trade) is True


def test_paper_challenger_is_protected():
    trade = {"id": "E0161", "execution_type": "paper_challenger", "signal_data": None}
    assert is_local_only_paper_trade(trade) is True


def test_paper_trade_with_exchange_order_id_is_reconcilable():
    sd = json.dumps({"entry_exchange_order_id": "123456"})
    trade = {"id": "X", "execution_type": "paper", "signal_data": sd}
    assert trade_reached_exchange(trade) is True
    assert is_local_only_paper_trade(trade) is False


def test_paper_trade_with_client_order_id_is_reconcilable():
    sd = json.dumps({"entry_exchange_client_order_id": "cloid-abc"})
    trade = {"id": "X", "execution_type": "paper_challenger", "signal_data": sd}
    assert is_local_only_paper_trade(trade) is False


def test_live_trade_is_not_local_paper():
    trade = {"id": "L1", "execution_type": "live", "signal_data": "{}"}
    assert is_local_only_paper_trade(trade) is False


def test_real_execution_type_is_not_local_paper():
    trade = {"id": "R1", "execution_type": "real", "signal_data": "{}"}
    assert is_local_only_paper_trade(trade) is False


def test_placeholder_order_ids_do_not_count_as_reached():
    for placeholder in ("", "None", "null", "0"):
        sd = json.dumps({"entry_exchange_order_id": placeholder})
        trade = {"id": "X", "execution_type": "paper", "signal_data": sd}
        assert trade_reached_exchange(trade) is False
        assert is_local_only_paper_trade(trade) is True


def test_missing_execution_type_is_not_local_paper():
    # Unknown execution_type must not be silently protected.
    trade = {"id": "U1", "signal_data": "{}"}
    assert is_local_only_paper_trade(trade) is False
