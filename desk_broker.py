"""Brokers: the only code that touches an exchange.

Deliberately dumb. A broker answers a quote, takes an order, reports what it
holds, and reports its cash. It has no opinion about *whether* to trade — that
belongs to the judge — and it does no sizing — that belongs to SIZE.

``PaperBroker`` is the default and is not a stub: it fills at the live price
plus modelled slippage and charges the same fees the backtest charges, so a
paper run and a real run are directly comparable. Venues degrade here rather
than in the runner, which keeps the degrade decision in one file.

``AlpacaBroker`` speaks the REST API with plain ``urllib`` — no SDK — and
honours ``ALPACA_PAPER`` so a paper key and a live key are the same code path.
``RobinhoodBroker`` is the other crypto path: a custodial account instead of a
self-custody wallet, chosen with ``DESK_CRYPTO_BROKER=robinhood``, and signed
per request because that API accepts no unsigned call.
"""

from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol

from config import (
    ALPACA_BASE_URL,
    ALPACA_KEY_ID,
    ALPACA_SECRET_KEY,
    DESK_CRYPTO_BROKER,
    DESK_FEES_BPS,
    DESK_SLIPPAGE_BPS,
    desk_costs,
    JUPITER_API_URL,
    ROBINHOOD_API_KEY,
    ROBINHOOD_BASE_URL,
    ROBINHOOD_PRIVATE_KEY,
    SOLANA_KEYPAIR_PATH,
    SOLANA_MAX_PRICE_IMPACT_PCT,
    SOLANA_MIN_SOL,
    SOLANA_PRIVATE_KEY,
    SOLANA_QUOTE_TTL_SECONDS,
    SOLANA_RPC_URL,
    SOLANA_SLIPPAGE_BPS,
    SOLANA_WALLET_ADDRESS,
)
from desk_ledger import Fill, Position
from desk_solana import LAMPORTS_PER_SOL, SOL_MINT

USER_AGENT = "AetherDesk/1.0"


class BrokerError(RuntimeError):
    """Raised when a broker cannot act. The runner logs and moves on."""


@dataclass
class Order:
    """A sized intent. The broker turns this into a fill or a refusal."""

    symbol: str
    side: str  # "buy" | "sell"
    qty: float
    price: float = 0.0  # reference price; the broker decides the actual fill
    venue: str = ""
    mode: str = "paper"
    mint: str = ""  # Solana mint address, when the venue needs one


class Broker(Protocol):
    name: str
    mode: str

    def cash(self) -> float: ...
    def positions(self) -> list[Position]: ...
    def submit(self, order: Order) -> Optional[Fill]: ...


# ---------------------------------------------------------------------------
# Paper — the default everywhere, and never a stub
# ---------------------------------------------------------------------------
@dataclass
class PaperBroker:
    """Simulated fills at live prices, with the same costs as the backtest.

    Holding cash and positions in memory keeps a paper run honest: a paper
    account that can overspend would let the desk take positions it could
    never afford live.
    """

    name: str = "paper"
    mode: str = "paper"
    starting_cash: float = 10_000.0
    fees_bps: float = DESK_FEES_BPS
    slippage_bps: float = DESK_SLIPPAGE_BPS
    _cash: float = field(default=None, repr=False)
    _positions: dict[str, Position] = field(default_factory=dict, repr=False)
    fills: list[Fill] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self._cash is None:
            self._cash = float(self.starting_cash)

    # -- read ---------------------------------------------------------------
    def cash(self) -> float:
        return float(self._cash or 0.0)

    def positions(self) -> list[Position]:
        return [p for p in self._positions.values() if p.qty > 0]

    def equity(self, marks: Optional[dict[str, float]] = None) -> float:
        """Cash plus positions marked at ``marks`` (default: cost basis)."""
        marks = marks or {}
        value = sum(
            p.qty * marks.get(p.symbol, p.avg_price) for p in self.positions()
        )
        return self.cash() + value

    def position(self, symbol: str) -> Optional[Position]:
        return self._positions.get(symbol)

    # -- restart ------------------------------------------------------------
    def replay(self, fills: list[Fill]) -> None:
        """Rebuild cash from historical fills *without* recording new ones.

        A paper account lives on the ledger, not in this object, so a restart
        must fold yesterday's cash flows back in. Applying only the side of the
        arithmetic here and letting :meth:`adopt` supply the holdings keeps one
        rule: cash comes from the fills, the open book comes from the positions
        table.
        """
        for fill in fills:
            if fill.qty <= 0 or fill.price <= 0:
                continue
            notional = fill.qty * fill.price
            if fill.side == "buy":
                self._cash = self.cash() - notional - fill.fees
            elif fill.side == "sell":
                self._cash = self.cash() + notional - fill.fees

    def adopt(self, positions: list[Position]) -> None:
        """Take an authoritative open book (stops and targets included)."""
        self._positions = {p.symbol: p for p in positions if p.qty > 0}

    # -- write --------------------------------------------------------------
    def submit(self, order: Order) -> Optional[Fill]:
        if order.qty <= 0 or order.price <= 0:
            return None
        cost_rate = self.fees_bps / 10_000.0
        slip_rate = self.slippage_bps / 10_000.0

        if order.side == "buy":
            price = order.price * (1 + slip_rate)
            notional = order.qty * price
            fees = notional * cost_rate
            if notional + fees > self.cash() + 1e-9:
                return None  # cannot afford it; refuse rather than overdraw
            self._cash = self.cash() - notional - fees
            existing = self._positions.get(order.symbol)
            if existing and existing.qty > 0:
                total = existing.qty + order.qty
                avg = (existing.avg_price * existing.qty + price * order.qty) / total
                existing.qty, existing.avg_price = total, avg
            else:
                self._positions[order.symbol] = Position(
                    symbol=order.symbol, qty=order.qty, avg_price=price,
                    venue=order.venue, mode=self.mode,
                )
        elif order.side == "sell":
            held = self._positions.get(order.symbol)
            if not held or held.qty <= 0:
                return None
            qty = min(order.qty, held.qty)
            price = order.price * (1 - slip_rate)
            notional = qty * price
            fees = notional * cost_rate
            self._cash = self.cash() + notional - fees
            held.qty -= qty
            if held.qty <= 1e-12:
                self._positions.pop(order.symbol, None)
        else:
            return None

        fill = Fill(  # a sell is clamped to what is held; record what was sold
            symbol=order.symbol, side=order.side,
            qty=qty if order.side == "sell" else order.qty, price=price,
            fees=fees, venue=order.venue, broker=self.name, mode=self.mode,
        )
        self.fills.append(fill)
        return fill


# ---------------------------------------------------------------------------
# Alpaca — stocks, paper and live through one code path
# ---------------------------------------------------------------------------
@dataclass
class AlpacaBroker:
    """US equities via REST. ``ALPACA_PAPER`` picks the host, not the code."""

    name: str = "alpaca"
    mode: str = "paper"
    base_url: str = ALPACA_BASE_URL
    key_id: str = ALPACA_KEY_ID
    secret: str = ALPACA_SECRET_KEY
    timeout: int = 20

    @property
    def configured(self) -> bool:
        return bool(self.key_id and self.secret)

    def _call(self, path: str, method: str = "GET", body: Optional[dict] = None) -> dict:
        if not self.configured:
            raise BrokerError("ALPACA_KEY_ID / ALPACA_SECRET_KEY are not set")
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(
            f"{self.base_url}{path}", data=data, method=method,
            headers={
                "APCA-API-KEY-ID": self.key_id,
                "APCA-API-SECRET-KEY": self.secret,
                "Content-Type": "application/json",
                "User-Agent": USER_AGENT,
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:200]
            raise BrokerError(f"Alpaca HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise BrokerError(f"Alpaca unreachable: {exc}") from exc
        try:
            return json.loads(raw) if raw.strip() else {}
        except ValueError as exc:
            raise BrokerError(f"Alpaca sent non-JSON: {raw[:120]!r}") from exc

    def cash(self) -> float:
        account = self._call("/v2/account")
        return float(account.get("cash") or 0.0)

    def positions(self) -> list[Position]:
        rows = self._call("/v2/positions") or []
        out = []
        for row in rows if isinstance(rows, list) else []:
            try:
                out.append(Position(
                    symbol=row.get("symbol", ""), qty=float(row.get("qty") or 0.0),
                    avg_price=float(row.get("avg_entry_price") or 0.0),
                    venue="stocks", mode=self.mode,
                ))
            except (TypeError, ValueError):
                continue
        return out

    def submit(self, order: Order) -> Optional[Fill]:
        """Market order. qty is rounded to whole shares — Alpaca's default."""
        qty = max(1, int(order.qty)) if order.qty >= 1 else order.qty
        payload = {
            "symbol": order.symbol,
            "qty": str(qty),
            "side": order.side,
            "type": "market",
            "time_in_force": "day",
        }
        try:
            ack = self._call("/v2/orders", method="POST", body=payload)
        except BrokerError:
            return None
        return Fill(
            symbol=order.symbol, side=order.side, qty=float(qty),
            price=float(ack.get("filled_avg_price") or order.price or 0.0),
            venue="stocks", broker=self.name, mode=self.mode,
            order_id=str(ack.get("id") or ""),
        )


# ---------------------------------------------------------------------------
# Jupiter — Solana memecoins, live
# ---------------------------------------------------------------------------
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"


@dataclass
class JupiterBroker:
    """Solana swaps through Jupiter, signed locally.

    Certainty comes from the ledger, not the chain: the chain says *what* is
    held, but has no idea what it cost. The runner hands in the mint map and
    the cost basis, both of which live in the desk's own ledger.
    """

    name: str = "jupiter"
    mode: str = "live"
    rpc_url: str = SOLANA_RPC_URL
    private_key: str = SOLANA_PRIVATE_KEY
    keypair_path: str = SOLANA_KEYPAIR_PATH
    wallet_address: str = SOLANA_WALLET_ADDRESS
    api_url: str = JUPITER_API_URL
    slippage_bps: int = SOLANA_SLIPPAGE_BPS
    max_impact_pct: float = SOLANA_MAX_PRICE_IMPACT_PCT
    min_sol: float = SOLANA_MIN_SOL
    timeout: int = 25

    #: ``symbol -> mint``, filled by the runner from the candidate ledger.
    symbols: dict[str, str] = field(default_factory=dict, repr=False)
    #: ``symbol -> average entry price``, filled from the open book.
    costs: dict[str, float] = field(default_factory=dict, repr=False)
    _keypair: Optional[object] = field(default=None, repr=False)
    _sol_price: tuple[float, float] = field(default=(0.0, 0.0), repr=False)

    # -- setup --------------------------------------------------------------
    @property
    def can_trade(self) -> bool:
        """A key is loaded and parses, so orders can be signed."""
        try:
            return self.keypair is not None
        except BrokerError:
            return False

    @property
    def configured(self) -> bool:
        """Readable: a key, or at least an address to watch."""
        return self.can_trade or bool(self.wallet_address)

    @property
    def keypair(self):
        if self._keypair is None:
            secret = self.private_key
            if not secret and self.keypair_path:
                try:
                    from pathlib import Path

                    secret = Path(self.keypair_path).read_text(encoding="utf-8")
                except OSError as exc:
                    raise BrokerError(f"cannot read SOLANA_KEYPAIR_PATH: {exc}") from exc
            if not secret:
                raise BrokerError("SOLANA_PRIVATE_KEY / SOLANA_KEYPAIR_PATH not set")
            try:
                from desk_solana import load_keypair

                self._keypair = load_keypair(secret)
            except Exception as exc:  # noqa: BLE001
                raise BrokerError(f"unusable Solana key: {exc}") from exc
        return self._keypair

    @property
    def address(self) -> str:
        """The wallet to read. A loaded key wins over the configured address."""
        try:
            return self.keypair.address
        except BrokerError:
            return self.wallet_address

    @property
    def rpc(self):
        from desk_solana import SolanaRpc

        return SolanaRpc(self.rpc_url, timeout=self.timeout)

    def mint_for(self, symbol: str) -> str:
        mint = self.symbols.get(symbol, "")
        if not mint:
            raise BrokerError(f"no Solana mint known for {symbol!r}")
        return mint

    # -- http ---------------------------------------------------------------
    def _request(self, path: str, *, params: dict = None,
                 body: dict = None) -> dict:
        url = f"{self.api_url}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(
            url, data=data, method="POST" if data else "GET",
            headers={"User-Agent": USER_AGENT, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
            raise BrokerError(f"Jupiter HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise BrokerError(f"Jupiter unreachable: {exc}") from exc
        try:
            return json.loads(raw) if raw.strip() else {}
        except ValueError as exc:
            raise BrokerError(f"Jupiter sent non-JSON: {raw[:120]!r}") from exc

    def quote(self, source: str, target: str, amount: int) -> dict:
        if amount < 1:
            raise BrokerError("quote amount rounds to zero")
        quote = self._request("/quote", params={
            "inputMint": source, "outputMint": target, "amount": int(amount),
            "slippageBps": int(self.slippage_bps),
            "restrictIntermediateTokens": "true",
        })
        impact = abs(_float(quote.get("priceImpactPct")))
        if impact > self.max_impact_pct:
            raise BrokerError(f"price impact {impact:.2f}% over "
                              f"{self.max_impact_pct:.2f}% cap")
        return quote

    def _swap(self, quote: dict) -> str:
        built = self._request("/swap", body={
            "quoteResponse": quote, "userPublicKey": self.keypair.address,
            "wrapAndUnwrapSol": True, "dynamicComputeUnitLimit": True,
        })
        transaction = built.get("swapTransaction") or ""
        if not transaction:
            raise BrokerError(f"Jupiter built no transaction "
                              f"({built.get('simulationError') or 'no detail'})")
        from desk_solana import sign_transaction

        return sign_transaction(transaction, self.keypair)

    # -- money --------------------------------------------------------------
    def sol_price(self) -> float:
        """SOL in USD, cached for the quote TTL. Zero when it cannot be read."""
        price, at = self._sol_price
        if price and time.time() - at < SOLANA_QUOTE_TTL_SECONDS:
            return price
        try:
            quote = self._request("/quote", params={
                "inputMint": SOL_MINT, "outputMint": USDC_MINT,
                "amount": LAMPORTS_PER_SOL, "slippageBps": 50,
            })
            price = int(quote.get("outAmount") or 0) / 1_000_000  # USDC has 6dp
        except BrokerError:
            price = 0.0
        self._sol_price = (price, time.time())
        return price

    def cash(self) -> float:
        """The free SOL balance, in USD, minus the fee reserve."""
        price = self.sol_price()
        if price <= 0:
            return 0.0
        free = (self.rpc.lamports(self.address) / LAMPORTS_PER_SOL
                - self.min_sol)
        return max(0.0, free) * price

    def positions(self) -> list[Position]:
        held = self.rpc.token_balances(self.address)
        by_mint = {mint: symbol for symbol, mint in self.symbols.items()}
        out = []
        for mint, (amount, _decimals) in held.items():
            if mint == SOL_MINT:
                continue
            symbol = by_mint.get(mint, mint)
            out.append(Position(symbol=symbol, qty=amount,
                                avg_price=self.costs.get(symbol, 0.0),
                                venue="crypto", mode=self.mode))
        return out

    def equity(self) -> float:
        return self.cash() + sum(p.qty * p.avg_price for p in self.positions())

    def position(self, symbol: str) -> Optional[Position]:
        return next((p for p in self.positions() if p.symbol == symbol), None)

    # -- trading ------------------------------------------------------------
    def submit(self, order: Order) -> Optional[Fill]:
        if not self.can_trade:
            return None  # watched address only; nothing can be signed
        try:
            if order.side == "buy":
                return self._buy(order)
            if order.side == "sell":
                return self._sell(order)
        except Exception:  # noqa: BLE001 — a bad swap must not kill the cycle
            return None
        return None

    def _buy(self, order: Order) -> Optional[Fill]:
        price = self.sol_price()
        if price <= 0:
            return None
        mint = order.mint or self.mint_for(order.symbol)
        # ``qty`` is a token amount; ``qty * order.price`` is the notional cost.
        notional = order.qty * (order.price or 0.0)
        if notional <= 0:
            return None
        lamports = int(notional / price * LAMPORTS_PER_SOL)
        ceiling = int(max(0.0, self.rpc.lamports(self.address)
                          / LAMPORTS_PER_SOL - self.min_sol) * LAMPORTS_PER_SOL)
        lamports = min(lamports, ceiling)
        if lamports < 1_000_000:  # below 0.001 SOL is not worth the fee
            return None

        quote = self.quote(SOL_MINT, mint, lamports)
        transaction = self._swap(quote)
        out_amount = int(quote.get("outAmount") or 0)
        if out_amount <= 0:
            return None
        # From the mint, not the wallet: a first buy has no token account yet.
        decimals = self.rpc.token_decimals(mint)
        tokens = out_amount / (10 ** decimals)
        spent = price * lamports / LAMPORTS_PER_SOL
        return Fill(
            symbol=order.symbol, side="buy", qty=tokens,
            price=spent / tokens if tokens else 0.0,
            venue="crypto", broker=self.name, mode=self.mode,
            order_id=self.rpc.send(transaction),
        )

    def _sell(self, order: Order) -> Optional[Fill]:
        mint = order.mint or self.mint_for(order.symbol)
        amount, decimals = self.rpc.token_balances(self.address).get(
            mint, (0.0, 0))
        qty = min(order.qty, amount)
        if qty <= 0:
            return None
        base_units = int(qty * 10 ** decimals)
        quote = self.quote(mint, SOL_MINT, base_units)
        transaction = self._swap(quote)
        out_lamports = int(quote.get("outAmount") or 0)
        price = self.sol_price()
        if price <= 0 or out_lamports <= 0:
            return None
        return Fill(
            symbol=order.symbol, side="sell", qty=qty,
            price=(out_lamports / LAMPORTS_PER_SOL) * price / qty,
            venue="crypto", broker=self.name, mode=self.mode,
            order_id=self.rpc.send(transaction),
        )


# ---------------------------------------------------------------------------
# Robinhood — crypto on a custodial account, signed per request
# ---------------------------------------------------------------------------
class RobinhoodBroker:
    """Crypto via Robinhood's Trading API.

    Every request is signed, not just the keyed ones: the wire format is an
    Ed25519 signature over ``api_key + timestamp + path + method + body``,
    base64-encoded into ``x-signature``. The signature therefore covers the
    *exact* body that is sent, so the body is serialised once and both signed
    and transmitted from that same string.

    The path precedes the method. Published summaries of this API often give
    the reverse order, and a reversed string signs fine and verifies fine
    locally — it just 401s on the wire, which is how it was found.

    Symbol format is the pair (``BTC-USD``), and Robinhood calls the open book
    "holdings" — the name is theirs, the shape is :class:`Position`.
    """

    name: str = "robinhood"
    mode: str = "live"
    base_url: str = ROBINHOOD_BASE_URL
    api_key: str = ROBINHOOD_API_KEY
    private_key: str = ROBINHOOD_PRIVATE_KEY
    timeout: int = 25

    def __init__(self, mode: str = "live", **overrides: Any) -> None:
        self.mode = mode
        self.base_url = overrides.get("base_url", ROBINHOOD_BASE_URL)
        self.api_key = overrides.get("api_key", ROBINHOOD_API_KEY)
        self.private_key = overrides.get("private_key", ROBINHOOD_PRIVATE_KEY)
        self.timeout = int(overrides.get("timeout", 25))
        self._signing: Optional[Any] = None

    # -- setup --------------------------------------------------------------
    @property
    def signing_key(self):
        if self._signing is None:
            if not self.api_key:
                raise BrokerError("ROBINHOOD_API_KEY is not set")
            if not self.private_key:
                raise BrokerError("ROBINHOOD_PRIVATE_KEY is not set")
            try:
                from nacl.signing import SigningKey

                raw = base64.b64decode(self.private_key.strip(), validate=True)
            except Exception as exc:  # noqa: BLE001
                raise BrokerError(f"unusable Robinhood key: {exc}") from exc
            # Either the bare 32-byte seed or seed+public (64) is accepted.
            raw = raw[:32]
            if len(raw) != 32:
                raise BrokerError("ROBINHOOD_PRIVATE_KEY is not a base64 Ed25519 seed")
            self._signing = SigningKey(raw)
        return self._signing

    @property
    def can_trade(self) -> bool:
        """Both halves are present and the private one parses."""
        try:
            return self.signing_key is not None
        except BrokerError:
            return False

    @property
    def configured(self) -> bool:
        # Reading also needs a signature, so an API key alone reads nothing.
        return self.can_trade

    # -- transport ----------------------------------------------------------
    def _call(self, path: str, method: str = "GET", body: Optional[dict] = None) -> Any:
        payload = json.dumps(body) if body is not None else ""
        timestamp = str(int(time.time()))
        message = f"{self.api_key}{timestamp}{path}{method}{payload}".encode()
        headers = {
            "x-api-key": self.api_key,
            "x-timestamp": timestamp,
            "x-signature": base64.b64encode(
                self.signing_key.sign(message).signature
            ).decode(),
            "User-Agent": USER_AGENT,
        }
        data = payload.encode() if body is not None else None
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            f"{self.base_url}{path}", data=data, method=method, headers=headers,
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:200]
            raise BrokerError(f"Robinhood HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise BrokerError(f"Robinhood unreachable: {exc}") from exc
        if not raw.strip():
            return {}
        try:
            return json.loads(raw)
        except ValueError as exc:
            raise BrokerError(f"Robinhood sent non-JSON: {raw[:120]!r}") from exc

    @staticmethod
    def _rows(payload: Any) -> list[dict]:
        """Unwrap a ``{"results": [...]}`` envelope, or take a bare list."""
        if isinstance(payload, dict):
            payload = payload.get("results") or payload.get("data") or []
        return [row for row in payload if isinstance(row, dict)] if isinstance(payload, list) else []

    # -- read ---------------------------------------------------------------
    def cash(self) -> float:
        for row in self._rows(self._call("/api/v1/crypto/trading/accounts/")):
            value = _float(row.get("buying_power")) or _float(row.get("cash"))
            if value:
                return value
        return 0.0

    def positions(self) -> list[Position]:
        out = []
        for row in self._rows(self._call("/api/v1/crypto/trading/holdings/")):
            qty = _float(row.get("quantity"))
            if qty <= 0:
                continue
            symbol = str(row.get("symbol") or "")
            if not symbol:
                currency = row.get("currency")
                code = row.get("asset_code") or (
                    currency.get("code") if isinstance(currency, dict) else "")
                symbol = f"{code}-USD" if code else ""
            if not symbol:
                continue
            out.append(Position(
                symbol=symbol, qty=qty,
                avg_price=_float(row.get("average_buy_price")),
                venue="crypto", mode=self.mode,
            ))
        return out

    # -- write --------------------------------------------------------------
    def submit(self, order: Order) -> Optional[Fill]:
        if order.qty <= 0:
            return None
        path = "/api/v1/crypto/trading/orders/"
        quantity = f"{order.qty:.8f}".rstrip("0").rstrip(".")
        # Robinhood trades pairs ("BONK-USD"); a bare DexScreener symbol was refused.
        pair = order.symbol if "-" in order.symbol else f"{order.symbol}-USD"
        body = {
            "client_order_id": uuid.uuid4().hex,
            "side": order.side,
            "symbol": pair,
            "type": "market",
            "market_order_config": {"asset_quantity": quantity},
        }
        try:
            ack = self._call(path, method="POST", body=body)
        except BrokerError as exc:
            # The older equities-shaped body is still accepted on some
            # accounts; a 400 is the only signal we get, and it is harmless
            # to try the other spelling once.
            if "HTTP 400" not in str(exc):
                return None
            legacy = {
                "side": order.side, "symbol": pair, "type": "market",
                "quantity": quantity, "time_in_force": "gfd", "trigger": "immediate",
            }
            try:
                ack = self._call(path, method="POST", body=legacy)
            except BrokerError:
                return None
        if not isinstance(ack, dict):
            return None
        return Fill(
            symbol=order.symbol, side=order.side, qty=order.qty,
            price=_float(ack.get("average_price")) or _float(ack.get("price")) or order.price,
            venue="crypto", broker=self.name, mode=self.mode,
            order_id=str(ack.get("id") or ack.get("client_order_id") or ""),
        )


def _float(value: Any, fallback: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


# ---------------------------------------------------------------------------
def make_broker(venue: str, mode: str = "paper", *, starting_cash: float = 10_000.0):
    """Pick a broker, degrading to paper when credentials are missing.

    Degrading *here* rather than in the runner is deliberate: one file decides
    whether real money moves, and the answer is written into every fill's
    ``mode`` so the ledger never has to guess.
    """
    fees, slippage = desk_costs(venue)
    paper = lambda: PaperBroker(starting_cash=starting_cash,
                                fees_bps=fees, slippage_bps=slippage)
    if venue == "stocks" and mode == "live":
        broker = AlpacaBroker(mode="live")
        if broker.configured:
            return broker
        return paper()
    if venue == "crypto" and mode == "live":
        # ``can_trade``, not ``configured``: an address alone can be watched
        # but not signed, and a desk that skips every candidate is worse than
        # one that keeps filling on paper.
        broker = (RobinhoodBroker() if DESK_CRYPTO_BROKER == "robinhood"
                  else JupiterBroker())
        if broker.can_trade:
            return broker
        return paper()
    return paper()


def resolve_mode(venue: str, requested: str) -> str:
    """The mode a venue will *actually* run in, after degradation."""
    return make_broker(venue, requested).mode
