"""Shadow mode: real Robinhood reads and quotes, simulated orders."""

import desk_broker
from desk_broker import BrokerError, Order, ShadowBroker, make_broker
from tests.test_desk_broker_robinhood import keyed


class FakeLive:
    def __init__(self, price=100.0, fail=False):
        self.price, self.fail, self.quoted = price, fail, []

    def quote(self, symbol, side="buy"):
        self.quoted.append((symbol, side))
        if self.fail:
            raise BrokerError("Robinhood HTTP 401: bad signature")
        return self.price

    def submit(self, order):  # pragma: no cover — must never be reached
        raise AssertionError("shadow mode must never place a real order")


def test_shadow_fills_on_paper_at_robinhoods_quote_and_never_orders():
    live = FakeLive(price=50.0)
    b = ShadowBroker(starting_cash=1_000, fees_bps=10, slippage_bps=50, live=live)
    fill = b.submit(Order(symbol="BTC-USD", side="buy", qty=2, price=999.0, venue="crypto"))
    assert fill.price == 50.0                        # Robinhood's ask, not our guess, no extra slippage
    assert fill.mode == "shadow" and fill.broker == "robinhood-shadow"
    assert round(b.cash(), 6) == round(1_000 - 100 - 0.1, 6)   # fees still charged
    assert live.quoted == [("BTC-USD", "buy")]


def test_a_failed_quote_refuses_the_order_and_says_why():
    b = ShadowBroker(starting_cash=1_000, live=FakeLive(fail=True))
    assert b.submit(Order(symbol="BTC-USD", side="buy", qty=1, price=1.0, venue="crypto")) is None
    assert "401" in b.last_error and b.cash() == 1_000


def test_make_broker_picks_shadow_only_with_usable_robinhood_keys(monkeypatch):
    monkeypatch.setattr(desk_broker, "DESK_CRYPTO_BROKER", "robinhood")
    monkeypatch.setattr(desk_broker.RobinhoodBroker, "can_trade", property(lambda self: True))
    assert isinstance(make_broker("crypto", "shadow"), ShadowBroker)
    assert make_broker("stocks", "shadow").mode == "paper"     # stocks have no shadow path
    monkeypatch.setattr(desk_broker.RobinhoodBroker, "can_trade", property(lambda self: False))
    assert make_broker("crypto", "shadow").mode == "paper"


def test_robinhood_quote_reads_the_spread_inclusive_side(monkeypatch):
    broker = keyed()
    monkeypatch.setattr(broker, "_call", lambda path, method="GET", body=None: {"results": [
        {"symbol": "BTC-USD", "price": "100", "ask_inclusive_of_buy_spread": "101",
         "bid_inclusive_of_sell_spread": "99"}]})
    assert broker.quote("BTC", "buy") == 101.0 and broker.quote("BTC-USD", "sell") == 99.0
