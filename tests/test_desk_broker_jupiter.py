"""JupiterBroker: the arithmetic and the refusals, with no network.

A memecoin wallet's two real dangers are spending the SOL needed for fees and
selling more than is actually held. Both are pinned here, along with the
attempts that must return a refusal (``None``) instead of a fill, because the
runner treats ``None`` as "no trade" and anything else would corrupt the
ledger.
"""

from __future__ import annotations

from nacl.signing import SigningKey

import desk_solana as S
from desk_broker import JupiterBroker, Order
from desk_solana import SOL_MINT

MINT = "9n4nbM75f5Ui33ZbPYXn59EwSgE8CGsHtAeTH5YFeJ9E"
OTHER = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"


def _keypair() -> S.Keypair:
    key = SigningKey.generate()
    return S.Keypair(key=key, address=S.b58encode(key.verify_key.encode()))


class FakeRpc:
    """Enough of SolanaRpc to answer the broker, and to remember what it sent."""

    def __init__(self, balance: float = 1.0, held: dict | None = None) -> None:
        self.balance = balance
        self.held = held or {}
        self.sent: list[str] = []

    def lamports(self, owner: str) -> int:
        return int(self.balance * 1_000_000_000)

    def token_balances(self, owner: str) -> dict[str, tuple[float, int]]:
        return dict(self.held)

    def token_decimals(self, mint: str) -> int:
        return 6

    def send(self, transaction: str) -> str:
        self.sent.append(transaction)
        return f"sig{len(self.sent)}"


def _broker(monkeypatch, *, balance: float = 1.0, held: dict | None = None,
            key: bool = True, sol_px: float = 100.0) -> tuple[JupiterBroker, FakeRpc, list]:
    """A broker wired to a fake RPC and a recording HTTP layer."""
    broker = JupiterBroker(min_sol=0.01, max_impact_pct=5.0)
    broker._keypair = _keypair() if key else None
    rpc = FakeRpc(balance=balance, held=held)
    monkeypatch.setattr(type(broker), "rpc", property(lambda self: rpc))

    quotes: list[dict] = []

    def request(path, *, params=None, body=None):
        if path == "/quote":
            quotes.append(dict(params or {}))
            src = (params or {}).get("inputMint")
            dst = (params or {}).get("outputMint")
            amount = int((params or {}).get("amount") or 0)
            if src == SOL_MINT and dst == OTHER:
                out = 100_000_000          # SOL/USD probe: $100 per SOL
            elif src == SOL_MINT:
                out = amount               # one token unit per lamport
            else:
                out = amount // 100
            return {"inputMint": src, "outAmount": out,
                    "priceImpactPct": "0.001"}
        if path == "/swap":
            return {"swapTransaction": "unsigned"}
        raise AssertionError(f"unexpected path {path}")

    broker._request = request
    monkeypatch.setattr(S, "sign_transaction", lambda tx, kp: "signed")
    return broker, rpc, quotes


# ---------------------------------------------------------------------------
# setup and refusals
# ---------------------------------------------------------------------------
def test_a_watched_address_is_configured_but_cannot_trade():
    broker = JupiterBroker(private_key="", keypair_path="",
                           wallet_address="CyeRXqWA8KSrtHUnB9QUDtgaw1bcTFtQEcrsjaJavaJJ")
    assert broker.configured is True
    assert broker.can_trade is False
    assert broker.submit(Order(symbol="DOGE", side="buy", qty=1, price=1)) is None


def test_a_buy_with_no_known_mint_is_refused(monkeypatch):
    broker, rpc, _ = _broker(monkeypatch)
    assert broker.submit(Order(symbol="NOPE", side="buy", qty=1, price=1)) is None
    assert rpc.sent == []


def test_a_buy_below_the_fee_floor_is_skipped(monkeypatch):
    broker, rpc, _ = _broker(monkeypatch)
    broker.symbols = {"TINY": MINT}
    assert broker.submit(Order(symbol="TINY", side="buy", qty=1, price=0.001)) is None
    assert rpc.sent == []


# ---------------------------------------------------------------------------
# buying
# ---------------------------------------------------------------------------
def test_a_buy_never_spends_the_fee_reserve(monkeypatch):
    broker, rpc, quotes = _broker(monkeypatch, balance=0.05)
    broker.symbols = {"PEPE": MINT}
    fill = broker.submit(Order(symbol="PEPE", side="buy", qty=1, price=1000.0))
    assert fill is not None
    # 0.05 SOL held, 0.01 reserved -> 0.04 SOL (40,000,000 lamports) at most.
    assert _swaps(quotes)[0]["amount"] == 40_000_000


def test_a_buy_reports_the_tokens_the_quote_promised(monkeypatch):
    broker, rpc, quotes = _broker(monkeypatch, balance=100.0,
                                  held={MINT: (0.0, 6)})
    broker.symbols = {"PEPE": MINT}
    fill = broker.submit(Order(symbol="PEPE", side="buy", qty=1, price=100.0))
    assert fill is not None
    # $100 at $100/SOL is 1 SOL; the fake pays one token unit per lamport.
    assert fill.qty == pytest_approx(1000.0)
    assert fill.side == "buy" and fill.mode == "live"
    assert rpc.sent == ["signed"]


def test_the_swap_is_signed_by_the_loaded_key(monkeypatch):
    broker, rpc, _ = _broker(monkeypatch, balance=100.0)
    broker.symbols = {"PEPE": MINT}
    sent: dict = {}

    def request(path, *, params=None, body=None):
        if path == "/quote":
            return {"outAmount": 1_000_000, "priceImpactPct": "0.0"}
        sent.update(body or {})
        return {"swapTransaction": "unsigned"}

    broker._request = request
    broker.submit(Order(symbol="PEPE", side="buy", qty=1, price=100.0))
    assert sent["userPublicKey"] == broker.keypair.address


# ---------------------------------------------------------------------------
# the impact cap
# ---------------------------------------------------------------------------
def test_a_quote_over_the_impact_cap_is_refused(monkeypatch):
    from desk_broker import BrokerError

    broker, rpc, quotes = _broker(monkeypatch)
    broker._request = lambda path, params=None, body=None: {
        "outAmount": 1, "priceImpactPct": "9.5"}
    try:
        broker.quote(SOL_MINT, MINT, 1_000_000)
    except BrokerError as exc:
        assert "price impact" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("the cap did not refuse")


# ---------------------------------------------------------------------------
# selling and reading
# ---------------------------------------------------------------------------
def test_a_sell_is_capped_at_what_is_held(monkeypatch):
    broker, rpc, quotes = _broker(monkeypatch, held={MINT: (12.0, 6)})
    broker.symbols = {"PEPE": MINT}
    fill = broker.submit(Order(symbol="PEPE", side="sell", qty=100))
    assert fill is not None
    assert fill.qty == 12.0
    assert _swaps(quotes)[0]["amount"] == 12_000_000   # base units at 6 decimals
    assert rpc.sent == ["signed"]


def test_a_sell_of_something_not_held_is_refused(monkeypatch):
    broker, rpc, quotes = _broker(monkeypatch, held={})
    broker.symbols = {"PEPE": MINT}
    assert broker.submit(Order(symbol="PEPE", side="sell", qty=5)) is None
    assert quotes == []


def test_positions_skip_sol_and_use_the_ledger_cost(monkeypatch):
    broker, rpc, _ = _broker(monkeypatch, held={SOL_MINT: (2.0, 9),
                                                MINT: (5.0, 6)})
    broker.symbols = {"PEPE": MINT}
    broker.costs = {"PEPE": 0.25}
    held = broker.positions()
    assert [p.symbol for p in held] == ["PEPE"]
    assert held[0].avg_price == 0.25


def test_cash_is_the_balance_minus_the_fee_reserve(monkeypatch):
    broker, rpc, _ = _broker(monkeypatch, balance=2.0, sol_px=100.0)
    assert broker.cash() == pytest_approx(199.0)


def test_an_unpriced_sol_reads_as_zero_cash(monkeypatch):
    broker, rpc, _ = _broker(monkeypatch)
    broker._sol_price = (0.0, 0.0)
    broker._request = lambda path, params=None, body=None: {}
    assert broker.cash() == 0.0


def _swaps(quotes: list[dict]) -> list[dict]:
    """Quotes other than the SOL/USD probe, which every pricing call emits."""
    return [q for q in quotes if q.get("outputMint") != OTHER]


def pytest_approx(value):
    import pytest

    return pytest.approx(value)
