"""Robinhood broker tests: the signing string, the mapping, and the degrade.

All offline. ``_call`` is replaced by a recorder in the tests that would
otherwise reach the network, and the signed message is checked against the
public half of a throwaway key pair — a signature that does not verify is the
one failure that would look like a working request until Robinhood rejects it.
"""

from __future__ import annotations

import base64
import json

import pytest
from nacl.signing import SigningKey

import desk_broker
from desk_broker import BrokerError, Order, RobinhoodBroker


def a_pair() -> tuple[str, str]:
    """A throwaway key pair as ``(private_b64, public_b64)``."""
    key = SigningKey.generate()
    return (base64.b64encode(bytes(key)).decode(),
            base64.b64encode(bytes(key.verify_key)).decode())


def keyed(**overrides) -> RobinhoodBroker:
    private, _ = a_pair()
    return RobinhoodBroker(api_key="rh-key", private_key=private, **overrides)


@pytest.fixture
def captured(monkeypatch):
    """Record the signed message and headers instead of sending them."""
    seen: list[dict] = []

    def fake_urlopen(request, timeout=None):
        seen.append({
            "url": request.full_url,
            "method": request.get_method(),
            "body": request.data.decode() if request.data else "",
            "key": request.get_header("X-api-key"),
            "signature": request.get_header("X-signature"),
            "timestamp": request.get_header("X-timestamp"),
        })

        class Reply:
            def read(self) -> bytes:
                return b"{}"

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return None

        return Reply()

    monkeypatch.setattr(desk_broker.urllib.request, "urlopen", fake_urlopen)
    return seen


# ---------------------------------------------------------------------------
# Signing
# ---------------------------------------------------------------------------
def test_the_signature_covers_key_timestamp_path_method_and_body(captured) -> None:
    private, public = a_pair()
    broker = RobinhoodBroker(api_key="rh-key", private_key=private)

    broker._call("/api/v1/crypto/trading/orders/", method="POST", body={"side": "buy"})

    sent = captured[0]
    # Literal order, not built the way the client builds it: path BEFORE method.
    message = (
        f"rh-key{sent['timestamp']}/api/v1/crypto/trading/orders/POST"
        f'{sent["body"]}'
    ).encode()
    from nacl.signing import VerifyKey

    # Raises BadSignatureError if the message or the signature is wrong.
    VerifyKey(base64.b64decode(public)).verify(
        message, base64.b64decode(sent["signature"])
    )


def test_the_method_after_path_ordering_is_the_only_accepted_one(captured) -> None:
    """A reversed string still signs and verifies, so pin the order itself.

    The suite stayed green while production signed method-then-path, because
    both sided with the same wrong summary. This checks the two differ and that
    the client produces the path-first one.
    """
    private, public = a_pair()
    broker = RobinhoodBroker(api_key="rh-key", private_key=private)

    broker._call("/api/v1/crypto/trading/orders/", method="POST", body={"side": "buy"})

    sent = captured[0]
    from nacl.signing import VerifyKey

    verify = VerifyKey(base64.b64decode(public))
    signature = base64.b64decode(sent["signature"])
    reversed_message = (
        f"rh-key{sent['timestamp']}POST/api/v1/crypto/trading/orders/"
        f'{sent["body"]}'
    ).encode()

    with pytest.raises(Exception):
        verify.verify(reversed_message, signature)


def test_the_body_that_is_signed_is_the_body_that_is_sent(captured) -> None:
    broker = keyed()

    broker._call("/api/v1/crypto/trading/orders/", method="POST", body={"side": "buy"})

    assert json.loads(captured[0]["body"]) == {"side": "buy"}


def test_a_get_signs_an_empty_body(captured) -> None:
    broker = keyed()

    broker._call("/api/v1/crypto/trading/accounts/")

    assert captured[0]["body"] == ""


def test_the_api_key_travels_in_its_own_header(captured) -> None:
    broker = keyed()

    broker._call("/api/v1/crypto/trading/accounts/")

    assert captured[0]["key"] == "rh-key"


# ---------------------------------------------------------------------------
# Readiness
# ---------------------------------------------------------------------------
def test_an_api_key_without_a_private_key_cannot_trade() -> None:
    broker = RobinhoodBroker(api_key="rh-key", private_key="")

    assert broker.can_trade is False


def test_no_key_at_all_cannot_trade() -> None:
    assert RobinhoodBroker(api_key="", private_key="").can_trade is False


def test_a_64_byte_key_is_cut_to_its_seed(captured) -> None:
    """seed+public blobs are accepted; only the seed half signs."""
    key = SigningKey.generate()
    blob = base64.b64encode(bytes(key) + bytes(key.verify_key)).decode()

    assert RobinhoodBroker(api_key="k", private_key=blob).can_trade is True


def test_an_unparseable_private_key_cannot_trade() -> None:
    assert RobinhoodBroker(api_key="k", private_key="not base64!!").can_trade is False


# ---------------------------------------------------------------------------
# Mapping
# ---------------------------------------------------------------------------
def test_cash_reads_buying_power() -> None:
    broker = keyed()
    broker._call = lambda *a, **k: {"results": [{"buying_power": "250.50", "cash": "250.50"}]}

    assert broker.cash() == pytest.approx(250.5)


def test_holdings_become_positions() -> None:
    broker = keyed()
    broker._call = lambda *a, **k: {"results": [
        {"symbol": "BTC-USD", "quantity": "0.5", "average_buy_price": "100"},
        {"symbol": "ETH-USD", "quantity": "0", "average_buy_price": "9"},
    ]}

    positions = broker.positions()

    assert [(p.symbol, p.qty, p.avg_price) for p in positions] == [("BTC-USD", 0.5, 100.0)]
    assert positions[0].venue == "crypto"


def test_a_holding_without_a_symbol_uses_the_currency_code(captured) -> None:
    broker = keyed()
    broker._call = lambda *a, **k: {"results": [{"currency": {"code": "SOL"}, "quantity": "2"}]}

    assert broker.positions()[0].symbol == "SOL-USD"


def test_a_zero_quantity_order_is_not_submitted() -> None:
    broker = keyed()
    broker._call = lambda *a, **k: pytest.fail("should not call")

    assert broker.submit(Order(symbol="BTC-USD", side="buy", qty=0)) is None


def test_a_quote_sized_order_uses_the_market_order_config() -> None:
    broker = keyed()
    bodies: list[dict] = []

    def fake(path, method="GET", body=None):
        bodies.append(body)
        return {"id": "abc", "average_price": "100"}

    broker._call = fake
    fill = broker.submit(Order(symbol="BTC-USD", side="buy", qty=0.001))

    assert bodies[0]["market_order_config"] == {"asset_quantity": "0.001"}
    assert bodies[0]["client_order_id"]
    assert fill.price == pytest.approx(100.0)


def test_a_400_retries_once_with_the_legacy_body() -> None:
    broker = keyed()
    bodies: list[dict] = []

    def fake(path, method="GET", body=None):
        bodies.append(body)
        if len(bodies) == 1:
            raise BrokerError("Robinhood HTTP 400: unknown field market_order_config")
        return {"id": "abc"}

    broker._call = fake
    broker.submit(Order(symbol="BTC-USD", side="buy", qty=1, price=50))

    assert "quantity" in bodies[1]
    assert "market_order_config" not in bodies[1]


def test_a_network_error_becomes_no_fill() -> None:
    broker = keyed()

    def fake(path, method="GET", body=None):
        raise BrokerError("Robinhood unreachable: refused")

    broker._call = fake

    assert broker.submit(Order(symbol="BTC-USD", side="buy", qty=1)) is None


# ---------------------------------------------------------------------------
# Venue wiring
# ---------------------------------------------------------------------------
def test_the_crypto_venue_can_choose_robinhood(monkeypatch) -> None:
    private, _ = a_pair()
    monkeypatch.setattr(desk_broker, "DESK_CRYPTO_BROKER", "robinhood")
    monkeypatch.setattr(desk_broker, "ROBINHOOD_API_KEY", "k")
    monkeypatch.setattr(desk_broker, "ROBINHOOD_PRIVATE_KEY", private)

    assert isinstance(desk_broker.make_broker("crypto", "live"), RobinhoodBroker)


def test_robinhood_without_a_private_key_degrades_to_paper(monkeypatch) -> None:
    monkeypatch.setattr(desk_broker, "DESK_CRYPTO_BROKER", "robinhood")
    monkeypatch.setattr(desk_broker, "ROBINHOOD_API_KEY", "k")
    monkeypatch.setattr(desk_broker, "ROBINHOOD_PRIVATE_KEY", "")

    broker = desk_broker.make_broker("crypto", "live")

    assert broker.mode == "paper"
    assert type(broker).__name__ == "PaperBroker"


# ---------------------------------------------------------------------------
# Venue costs — an ETF and a memecoin do not fill alike
# ---------------------------------------------------------------------------
def test_crypto_paper_fills_are_charged_more_than_stock_fills(monkeypatch) -> None:
    """Slippage is the real difference: a thin book moves under you."""
    from config import DESK_SLIPPAGE_BPS_CRYPTO, DESK_SLIPPAGE_BPS_STOCKS

    assert DESK_SLIPPAGE_BPS_CRYPTO > DESK_SLIPPAGE_BPS_STOCKS


def test_a_paper_broker_carries_its_venue_costs(monkeypatch) -> None:
    import config

    monkeypatch.setattr(config, "DESK_FEES_BPS_CRYPTO", 50.0)
    monkeypatch.setattr(desk_broker, "desk_costs", config.desk_costs)

    broker = desk_broker.make_broker("crypto", "paper")

    assert broker.fees_bps == 50.0


def test_stock_costs_are_the_stock_figures(monkeypatch) -> None:
    import config

    monkeypatch.setattr(config, "DESK_SLIPPAGE_BPS_STOCKS", 7.0)
    monkeypatch.setattr(desk_broker, "desk_costs", config.desk_costs)

    assert desk_broker.make_broker("stocks", "paper").slippage_bps == 7.0
