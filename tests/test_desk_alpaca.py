"""Alpaca: the host decides the label, and only a real fill is recorded."""

from dataclasses import dataclass

import desk_broker
from desk_broker import AlpacaBroker, Order, make_broker


def keyed(**kw):
    b = AlpacaBroker(key_id="k", secret="s", **kw)
    b.poll_seconds = 2
    return b


def test_alpacas_paper_host_is_never_recorded_as_live(monkeypatch):
    # A dataclass bakes its defaults in at class creation, so swap the class.
    @dataclass
    class OnHost(AlpacaBroker):
        key_id: str = "k"
        secret: str = "s"

    @dataclass
    class RealHost(OnHost):
        base_url: str = "https://api.alpaca.markets"

    @dataclass
    class PaperHost(OnHost):
        base_url: str = desk_broker.ALPACA_PAPER_URL

    monkeypatch.setattr(desk_broker, "AlpacaBroker", PaperHost)
    assert make_broker("stocks", "live").mode == "broker-paper"      # live, but on the paper host
    assert make_broker("stocks", "broker-paper").mode == "broker-paper"
    monkeypatch.setattr(desk_broker, "AlpacaBroker", RealHost)
    assert make_broker("stocks", "live").mode == "live"
    paper = make_broker("stocks", "broker-paper")
    assert paper.mode == "broker-paper" and paper.base_url == desk_broker.ALPACA_PAPER_URL


def test_a_fill_is_recorded_at_alpacas_price_after_polling(monkeypatch):
    b = keyed()
    replies = iter([{"id": "o1", "status": "accepted"},
                    {"id": "o1", "status": "filled", "filled_qty": "2", "filled_avg_price": "101.5"}])
    monkeypatch.setattr(b, "_call", lambda path, method="GET", body=None: next(replies))
    monkeypatch.setattr(desk_broker.time, "sleep", lambda s: None)
    fill = b.submit(Order(symbol="MSFT", side="buy", qty=2, price=100.0, venue="stocks"))
    assert fill.price == 101.5 and fill.qty == 2.0 and fill.order_id == "o1"


def test_an_unfilled_order_is_cancelled_not_recorded(monkeypatch):
    b = keyed()
    calls = []

    def call(path, method="GET", body=None):
        calls.append((method, path))
        return {"id": "o2", "status": "accepted", "filled_qty": "0"}

    clock = iter(range(0, 1000, 3))
    monkeypatch.setattr(b, "_call", call)
    monkeypatch.setattr(desk_broker.time, "sleep", lambda s: None)
    monkeypatch.setattr(desk_broker.time, "monotonic", lambda: next(clock))
    assert b.submit(Order(symbol="MSFT", side="buy", qty=1, price=100.0, venue="stocks")) is None
    assert ("DELETE", "/v2/orders/o2") in calls and "market open" in b.last_error
