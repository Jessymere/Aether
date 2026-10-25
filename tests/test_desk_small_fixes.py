"""Small ledger/broker fixes from the desk health check."""

import desk_broker
from desk_broker import Order, PaperBroker
from desk_ledger import Fill, closed_trades, connect, record_fill
from tests.test_desk_broker_robinhood import keyed


def test_a_paper_sell_records_the_quantity_actually_sold():
    b = PaperBroker(starting_cash=1000, fees_bps=0, slippage_bps=0)
    b.submit(Order(symbol="X", side="buy", qty=2, price=10, venue="stocks"))
    fill = b.submit(Order(symbol="X", side="sell", qty=5, price=10, venue="stocks"))
    assert fill.qty == 2


def test_robinhood_is_sent_a_pair_not_a_bare_symbol(monkeypatch):
    sent = []
    broker = keyed()
    monkeypatch.setattr(broker, "_call", lambda path, method="GET", body=None: sent.append(body) or {"id": "1"})
    broker.submit(Order(symbol="BONK", side="buy", qty=1, price=1.0, venue="crypto"))
    broker.submit(Order(symbol="BTC-USD", side="buy", qty=1, price=1.0, venue="crypto"))
    assert [b["symbol"] for b in sent] == ["BONK-USD", "BTC-USD"]


def test_trade_percent_is_net_of_both_legs_fees(tmp_path):
    conn = connect(tmp_path / "d.db")
    record_fill(conn, Fill(symbol="X", side="buy", qty=1, price=100, fees=1, venue="stocks", broker="paper", mode="paper"))
    record_fill(conn, Fill(symbol="X", side="sell", qty=1, price=102, fees=1, venue="stocks", broker="paper", mode="paper"))
    trade = closed_trades(conn, "paper", "stocks")[0]
    assert round(trade["pnl_pct"], 4) == 0.0   # +2 gross, -2 fees
